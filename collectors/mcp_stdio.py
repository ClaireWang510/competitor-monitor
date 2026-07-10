"""
Minimal MCP stdio client.

This module intentionally implements only the JSON-RPC calls needed by the
collector: initialize, tools/list and tools/call. Keeping it local avoids
coupling the project to a fast-moving MCP SDK API.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from asyncio.subprocess import PIPE, Process
from typing import Any, Optional

from loguru import logger


class MCPError(RuntimeError):
    """Raised when an MCP JSON-RPC call returns an error."""


class MCPStdioClient:
    """Small async JSON-RPC client for MCP servers using stdio transport."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str],
        timeout: int = 60,
        env: Optional[dict[str, str]] = None,
    ):
        self.name = name
        self.command = command
        self.args = args
        self.timeout = timeout
        self.env = env or {}
        self._process: Optional[Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._initialized = False

    async def connect(self) -> None:
        if self._initialized and self._is_running:
            return

        async with self._connect_lock:
            if self._initialized and self._is_running:
                return

            executable = shutil.which(self.command) or self.command
            try:
                self._process = await asyncio.create_subprocess_exec(
                    executable,
                    *self.args,
                    stdin=PIPE,
                    stdout=PIPE,
                    stderr=PIPE,
                    env={**os.environ, **self.env},
                )
            except FileNotFoundError as exc:
                raise MCPError(
                    f"MCP server {self.name} failed to start: command not found {self.command}"
                ) from exc

            self._reader_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._read_stderr())

            await self._send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "competitor-monitor",
                        "version": "0.1.0",
                    },
                },
            )
            await self._send_notification("notifications/initialized", {})
            self._initialized = True
            logger.debug(f"MCP server initialized: {self.name}")

    @property
    def _is_running(self) -> bool:
        return bool(self._process and self._process.returncode is None)

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.connect()

        tools: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._send_request("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.connect()
        return await self._send_request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._process or not self._process.stdin:
            raise MCPError(f"MCP server {self.name} is not running")

        request_id = self._next_id
        self._next_id += 1

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[request_id] = future

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        await self._write_message(payload)

        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise MCPError(
                f"MCP request timed out after {self.timeout}s: {self.name}.{method}"
            ) from exc
        finally:
            self._pending.pop(request_id, None)

    async def _send_notification(
        self, method: str, params: Optional[dict[str, Any]] = None
    ) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        await self._write_message(payload)

    async def _write_message(self, payload: dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise MCPError(f"MCP server {self.name} is not running")

        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            self._process.stdin.write(line.encode("utf-8"))
            await self._process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._process and self._process.stdout
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break

                try:
                    message = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.debug(
                        f"MCP server {self.name} emitted non-JSON stdout: {line[:200]!r}"
                    )
                    continue

                message_id = message.get("id")
                if message_id is None:
                    continue

                future = self._pending.get(message_id)
                if not future or future.done():
                    continue

                if "error" in message:
                    future.set_exception(MCPError(str(message["error"])))
                else:
                    future.set_result(message.get("result") or {})
        except Exception as exc:
            logger.debug(f"MCP stdout reader stopped for {self.name}: {exc}")
        finally:
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(MCPError(f"MCP server {self.name} stopped"))
            self._pending.clear()

    async def _read_stderr(self) -> None:
        assert self._process and self._process.stderr
        while True:
            line = await self._process.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                text = self._redact(text)
                logger.debug(f"MCP server {self.name}: {text}")

    @staticmethod
    def _redact(text: str) -> str:
        text = re.sub(r"Bearer\s+[^\"'\s]+", "Bearer ***", text)
        return re.sub(r'("Authorization"\s*:\s*")([^"]+)(")', r"\1***\3", text)

    async def close(self) -> None:
        if not self._process:
            return

        process = self._process
        self._process = None
        self._initialized = False

        if process.stdin:
            try:
                process.stdin.close()
            except Exception:
                pass

        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
