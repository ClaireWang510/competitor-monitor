"""
GitHubCollector —— 监控竞品相关的 GitHub 仓库活动
跟踪：新 Release、重要 PR、Issue 趋势、README 变更等
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from config.competitors import SourceConfig
from config.settings import settings
from models.data_models import RawItem

try:
    from github import Github, GithubException
except ImportError:
    Github = None
    GithubException = Exception


class GitHubCollector(BaseCollector):
    """
    通过 PyGithub 库采集竞品 GitHub 动态。
    对于配置了 github_repo 的 Source，抓取最新 Release 和近期热门 Issue/PR。
    """

    def __init__(self):
        token = settings.github.token
        self.gh = Github(token) if (Github and token) else None
        if not self.gh:
            logger.warning(
                "GitHubCollector: 未配置 GITHUB_TOKEN，将以匿名方式访问（速率限制 60 次/小时）"
            )
            self.gh = Github() if Github else None

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=10))
    async def _get_releases(
        self, org_name: str, since: Optional[datetime] = None
    ) -> List[RawItem]:
        """获取指定组织/用户的最新仓库 Release"""
        if not self.gh:
            return []

        items: List[RawItem] = []
        org = self.gh.get_organization(org_name)

        for repo in org.get_repos(sort="updated", direction="desc")[:10]:
            try:
                releases = repo.get_releases()
                for rel in releases[:3]:
                    if since and rel.published_at and rel.published_at < since:
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
            except Exception as e:
                logger.debug(f"GitHub: {org_name}/{repo.name} releases error: {e}")
                continue

        return items

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=10))
    async def _get_trending_issues(
        self, org_name: str, since: Optional[datetime] = None
    ) -> List[RawItem]:
        """获取近期热门 Issue（按评论数排序）"""
        if not self.gh:
            return []

        items: List[RawItem] = []
        query = f"org:{org_name} is:issue sort:comments-desc"
        if since:
            query += f" created:>{since.strftime('%Y-%m-%d')}"

        results = self.gh.search_issues(query)[:10]
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

    async def collect(
        self, source: SourceConfig, competitor_name: str = "", **kwargs
    ) -> List[RawItem]:
        """采集 GitHub 数据：Release + 热门 Issue"""
        if not self.gh:
            logger.error("GitHubCollector: PyGithub 未安装或未配置 Token")
            return []

        org_name = source.github_repo  # 此处可能是 org name 或 org/repo
        since = kwargs.get("since", datetime.utcnow() - timedelta(days=7))

        # 如果是 org/repo 格式，直接针对单个仓库
        if "/" in org_name:
            items = await self._get_single_repo_items(org_name, since)
        else:
            # 组织级别
            releases = await self._get_releases(org_name, since)
            issues = await self._get_trending_issues(org_name, since)
            items = releases + issues

        for item in items:
            item.competitor_name = competitor_name

        logger.debug(f"GitHubCollector: {org_name} -> {len(items)} items")
        return items

    async def _get_single_repo_items(
        self, full_name: str, since: Optional[datetime] = None
    ) -> List[RawItem]:
        """针对单个仓库的采集"""
        items: List[RawItem] = []
        try:
            repo = self.gh.get_repo(full_name)
            for rel in repo.get_releases()[:5]:
                if since and rel.published_at and rel.published_at < since:
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
        except Exception as e:
            logger.error(f"GitHub: {full_name} error: {e}")
        return items

    async def close(self):
        pass  # PyGithub 无需显式关闭
