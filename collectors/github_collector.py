"""
GitHubCollector —— 监控竞品相关的 GitHub 仓库活动
跟踪：新 Release、重要 PR、Issue 趋势、README 变更等
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from loguru import logger
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from config.competitors import SourceConfig
from config.settings import settings
from models.data_models import RawItem

try:
    from github import (
        Github,
        GithubException,
        RateLimitExceededException,
        UnknownObjectException,
    )
except ImportError:
    Github = None
    GithubException = Exception
    RateLimitExceededException = Exception
    UnknownObjectException = Exception


class GitHubCollector(BaseCollector):
    """
    通过 PyGithub 库采集竞品 GitHub 动态。
    对于配置了 github_repo 的 Source，抓取最新 Release 和近期热门 Issue/PR。
    """

    def __init__(self):
        token = settings.github.token
        # PyGithub 默认 retry 会在 403 rate limit 时按 reset/backoff 睡很久，
        # 对定时监控任务来说应快速失败并交给下一轮调度重试。
        github_kwargs = {
            "timeout": 10,
            "per_page": 10,
            "retry": 0,
        }
        self.gh = Github(token, **github_kwargs) if (Github and token) else None
        if not self.gh:
            logger.warning(
                "GitHubCollector: 未配置 GITHUB_TOKEN，将以匿名方式访问（速率限制 60 次/小时）"
            )
            self.gh = Github(**github_kwargs) if Github else None

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _is_before_since(cls, value: Optional[datetime], since: Optional[datetime]) -> bool:
        if not value or not since:
            return False
        normalized = cls._as_utc(value)
        since = cls._as_utc(since)
        return bool(normalized and since and normalized < since)

    def _get_owner_repos(self, owner_name: str) -> Tuple[object, str]:
        """获取 GitHub owner 的仓库列表，兼容 organization 和 user。"""
        try:
            org = self.gh.get_organization(owner_name)
            return org.get_repos(sort="updated", direction="desc"), "org"
        except RateLimitExceededException:
            raise
        except UnknownObjectException:
            user = self.gh.get_user(owner_name)
            return user.get_repos(sort="updated", direction="desc"), "user"

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_not_exception_type(RateLimitExceededException),
    )
    async def _get_releases(
        self, org_name: str, since: Optional[datetime] = None
    ) -> List[RawItem]:
        """获取指定组织/用户的最新仓库 Release"""
        if not self.gh:
            return []

        items: List[RawItem] = []
        repos, _ = self._get_owner_repos(org_name)

        for repo in repos[:5]:
            try:
                releases = repo.get_releases()
                for rel in releases[:2]:
                    if self._is_before_since(rel.published_at, since):
                        continue
                    items.append(
                        RawItem(
                            competitor_name="",
                            source_name=f"GitHub-{org_name}/{repo.name}",
                            source_type="github",
                            url=rel.html_url,
                            title=f"[Release] {repo.name} {rel.tag_name}",
                            content_snippet=(rel.body or "")[:800],
                            author=rel.author.login if rel.author else "",
                            published_at=rel.published_at,
                            raw_metadata={"repo": repo.full_name, "tag": rel.tag_name},
                        )
                    )
            except RateLimitExceededException:
                logger.warning(f"GitHub: {org_name}/{repo.name} rate limit exceeded, skip")
                break
            except Exception as e:
                logger.debug(f"GitHub: {org_name}/{repo.name} releases error: {e}")
                continue

        return items

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_not_exception_type(RateLimitExceededException),
    )
    async def _get_recent_commits(
        self, org_name: str, since: Optional[datetime] = None
    ) -> List[RawItem]:
        """获取组织/用户下近期有更新仓库的 commit 动态。"""
        if not self.gh:
            return []

        items: List[RawItem] = []
        repos, _ = self._get_owner_repos(org_name)

        for repo in repos[:5]:
            try:
                commits = repo.get_commits(since=since) if since else repo.get_commits()
                for commit in commits[:2]:
                    committed_at = self._as_utc(commit.commit.author.date)
                    if self._is_before_since(committed_at, since):
                        continue
                    message = (commit.commit.message or "").strip()
                    if not message:
                        continue
                    items.append(
                        RawItem(
                            competitor_name="",
                            source_name=f"GitHub-{repo.full_name}",
                            source_type="github",
                            url=commit.html_url,
                            title=f"[Commit] {repo.name}: {message.splitlines()[0][:160]}",
                            content_snippet=message[:800],
                            author=commit.author.login if commit.author else commit.commit.author.name,
                            published_at=committed_at,
                            raw_metadata={
                                "repo": repo.full_name,
                                "sha": commit.sha,
                                "kind": "commit",
                            },
                        )
                    )
            except RateLimitExceededException:
                logger.warning(f"GitHub: {org_name}/{repo.name} rate limit exceeded, skip")
                break
            except Exception as e:
                logger.debug(f"GitHub: {org_name}/{repo.name} commits error: {e}")
                continue

        return items

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_not_exception_type(RateLimitExceededException),
    )
    async def _get_trending_issues(
        self, org_name: str, since: Optional[datetime] = None
    ) -> List[RawItem]:
        """获取近期热门 Issue（按评论数排序）"""
        if not self.gh:
            return []

        items: List[RawItem] = []
        _, owner_kind = self._get_owner_repos(org_name)
        query = f"{owner_kind}:{org_name} is:issue sort:comments-desc"
        if since:
            query += f" created:>{since.strftime('%Y-%m-%d')}"

        results = self.gh.search_issues(query)[:5]
        for issue in results:
            items.append(
                RawItem(
                    competitor_name="",
                    source_name=f"GitHub-{org_name}",
                    source_type="github",
                    url=issue.html_url,
                    title=f"[Issue] {issue.title}",
                    content_snippet=(issue.body or "")[:800],
                    author=issue.user.login if issue.user else "",
                    published_at=issue.created_at,
                    raw_metadata={
                        "repo": issue.repository.full_name if issue.repository else "",
                        "comments": issue.comments,
                        "labels": [l.name for l in issue.labels],
                    },
                )
            )

        return items

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_not_exception_type(RateLimitExceededException),
    )
    async def _search_repositories(
        self, query: str, since: Optional[datetime] = None
    ) -> List[RawItem]:
        """按关键词搜索近期更新的开源项目，用于捕捉社区侧信号。"""
        if not self.gh or not query:
            return []

        items: List[RawItem] = []
        results = self.gh.search_repositories(
            query=query,
            sort="updated",
            order="desc",
        )

        for repo in results[:5]:
            if self._is_before_since(repo.pushed_at, since):
                continue

            description = repo.description or ""
            content = (
                f"{description}\n"
                f"Stars: {repo.stargazers_count}; Forks: {repo.forks_count}; "
                f"Open issues: {repo.open_issues_count}; "
                f"Updated: {repo.pushed_at.isoformat() if repo.pushed_at else ''}"
            ).strip()

            items.append(
                RawItem(
                    competitor_name="",
                    source_name=f"GitHub-Search",
                    source_type="github_search",
                    url=repo.html_url,
                    title=f"[Repo] {repo.full_name}",
                    content_snippet=content[:800],
                    author=repo.owner.login if repo.owner else "",
                    published_at=repo.pushed_at,
                    raw_metadata={
                        "repo": repo.full_name,
                        "query": query,
                        "stars": repo.stargazers_count,
                        "forks": repo.forks_count,
                    },
                )
            )

        return items

    async def collect(
        self, source: SourceConfig, competitor_name: str = "", **kwargs
    ) -> List[RawItem]:
        """采集 GitHub 数据：Release + 热门 Issue"""
        if not self.gh:
            logger.error("GitHubCollector: PyGithub 未安装或未配置 Token")
            return []

        org_name = source.github_repo  # 此处可能是 org name 或 org/repo
        since = self._as_utc(kwargs.get("since") or (self._utcnow() - timedelta(days=7)))

        if source.github_query:
            try:
                items = await self._search_repositories(source.github_query, since)
            except RateLimitExceededException:
                logger.warning(
                    f"GitHubCollector: {source.name} rate limit exceeded, skip this round"
                )
                return []
            for item in items:
                item.competitor_name = competitor_name
                item.source_name = source.name
            logger.debug(
                f"GitHubCollector: search {source.github_query} -> {len(items)} items"
            )
            return items

        if not org_name:
            logger.warning(f"GitHubCollector: {source.name} 未配置 github_repo 或 github_query")
            return []

        # 如果是 org/repo 格式，直接针对单个仓库
        if "/" in org_name:
            try:
                items = await self._get_single_repo_items(org_name, since)
            except RateLimitExceededException:
                logger.warning(
                    f"GitHubCollector: {org_name} rate limit exceeded, skip this round"
                )
                return []
        else:
            # 组织级别
            try:
                releases = await self._get_releases(org_name, since)
                issues = await self._get_trending_issues(org_name, since)
                commits = await self._get_recent_commits(org_name, since)
            except RateLimitExceededException:
                logger.warning(
                    f"GitHubCollector: {org_name} rate limit exceeded, skip this round"
                )
                return []
            items = releases + issues + commits

        for item in items:
            item.competitor_name = competitor_name
            item.source_name = source.name

        logger.debug(f"GitHubCollector: {org_name} -> {len(items)} items")
        return items

    async def _get_single_repo_items(
        self, full_name: str, since: Optional[datetime] = None
    ) -> List[RawItem]:
        """针对单个仓库的采集"""
        items: List[RawItem] = []
        try:
            repo = self.gh.get_repo(full_name)
            for rel in repo.get_releases()[:3]:
                if self._is_before_since(rel.published_at, since):
                    continue
                items.append(
                    RawItem(
                        competitor_name="",
                        source_name=f"GitHub-{full_name}",
                        source_type="github",
                        url=rel.html_url,
                        title=f"[Release] {rel.tag_name}: {rel.title}",
                        content_snippet=(rel.body or "")[:800],
                        author=rel.author.login if rel.author else "",
                        published_at=rel.published_at,
                        raw_metadata={"repo": full_name, "tag": rel.tag_name},
                    )
                )
            commits = repo.get_commits(since=since) if since else repo.get_commits()
            for commit in commits[:5]:
                committed_at = self._as_utc(commit.commit.author.date)
                if self._is_before_since(committed_at, since):
                    continue
                message = (commit.commit.message or "").strip()
                if not message:
                    continue
                items.append(
                    RawItem(
                        competitor_name="",
                        source_name=f"GitHub-{full_name}",
                        source_type="github",
                        url=commit.html_url,
                        title=f"[Commit] {message.splitlines()[0][:180]}",
                        content_snippet=message[:800],
                        author=commit.author.login if commit.author else commit.commit.author.name,
                        published_at=committed_at,
                        raw_metadata={"repo": full_name, "sha": commit.sha, "kind": "commit"},
                    )
                )
            issues = repo.get_issues(
                state="all",
                sort="updated",
                direction="desc",
                since=since,
            )
            for issue in issues[:5]:
                updated_at = self._as_utc(issue.updated_at or issue.created_at)
                if self._is_before_since(updated_at, since):
                    continue
                is_pr = bool(getattr(issue, "pull_request", None))
                prefix = "PR" if is_pr else "Issue"
                items.append(
                    RawItem(
                        competitor_name="",
                        source_name=f"GitHub-{full_name}",
                        source_type="github",
                        url=issue.html_url,
                        title=f"[{prefix}] {issue.title}",
                        content_snippet=(issue.body or "")[:800],
                        author=issue.user.login if issue.user else "",
                        published_at=updated_at,
                        raw_metadata={
                            "repo": full_name,
                            "number": issue.number,
                            "comments": issue.comments,
                            "state": issue.state,
                            "kind": "pull_request" if is_pr else "issue",
                            "labels": [l.name for l in issue.labels],
                        },
                    )
                )
        except RateLimitExceededException:
            raise
        except Exception as e:
            logger.error(f"GitHub: {full_name} error: {e}")
        return items

    async def close(self):
        pass  # PyGithub 无需显式关闭
