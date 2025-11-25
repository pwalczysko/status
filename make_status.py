# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyyaml",
#     "requests",
#     "tqdm"
# ]
# ///

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional
from yaml import load, dump, Loader
import requests
from tqdm import tqdm

with open("dashboard.yml") as f:
    config = load(f, Loader=Loader)

session = requests.Session()
session.headers.update(
    {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ome-status-dashboard",
    }
)

# Set via https://github.com/settings/personal-access-tokens
token = os.getenv("GITHUB_TOKEN")
if token:
    session.headers["Authorization"] = f"Bearer {token}"


def build_session() -> requests.Session:
    new_session = requests.Session()
    new_session.headers.update(session.headers)
    return new_session


def format_date(iso_timestamp: str) -> str:
    return (
        datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00")).date().isoformat()
    )


def fetch_last_commit_info(owner: str, repo: str, session: requests.Session) -> dict:
    """
    Fetch latest commit from the GitHub API.
    """
    base_url = f"https://api.github.com/repos/{owner}/{repo}/commits"

    latest_resp = session.get(base_url, params={"per_page": 1})
    if latest_resp.status_code == 404:
        return

    commits = latest_resp.json()

    last_commit = commits[0]
    url = last_commit.get("html_url")
    author_block = last_commit["commit"]["author"]
    date = format_date(author_block.get("date"))
    author = last_commit["author"]["login"]
    return {
        "url": url,
        "date": date,
        "author": author,
    }


def fetch_repo_info(owner: str, repo: str, session: requests.Session) -> Optional[dict]:
    """
    Fetch repository metadata from the GitHub API.
    """
    resp = session.get(f"https://api.github.com/repos/{owner}/{repo}")
    if resp.status_code == 404:
        return
    info = resp.json()
    return {
        "created_at": info.get("created_at"),
        "updated_at": info.get("updated_at"),
        "open_issues": info.get("open_issues_count"),
        "description": info.get("description"),
        "topics": info.get("topics", []),
        "size": info.get("size"),
    }


def fetch_last_release_info(
    owner: str, repo: str, session: requests.Session
) -> Optional[dict]:
    """
    Fetch latest release from the GitHub API.
    """
    releases_resp = session.get(
        f"https://api.github.com/repos/{owner}/{repo}/releases", params={"per_page": 1}
    )
    if releases_resp.status_code == 404:
        return None

    releases = releases_resp.json()
    if not releases:
        return None

    last_release = releases[0]
    published_at = last_release.get("published_at") or last_release.get("created_at")
    return {
        "url": last_release.get("html_url"),
        "tag_name": last_release.get("tag_name"),
        "date": format_date(published_at) if published_at else None,
    }


def process_package(package: dict) -> None:
    """
    Populate metadata for a single package. Runs in worker threads.
    """
    local_session = build_session()
    package["user"], package["name"] = package["repo"].split("/")

    repo_info = fetch_repo_info(package["user"], package["name"], local_session)
    if repo_info:
        package["repo_info"] = repo_info
    else:
        package["error"] = True

    last_commit_info = fetch_last_commit_info(
        package["user"], package["name"], local_session
    )
    if last_commit_info:
        package["last_commit"] = last_commit_info

    last_release_info = fetch_last_release_info(
        package["user"], package["name"], local_session
    )
    if last_release_info:
        package["last_release"] = last_release_info


all_packages: List[dict] = []
for section in config:
    all_packages.extend(section["packages"])

with ThreadPoolExecutor(max_workers=4) as executor:
    futures = [executor.submit(process_package, package) for package in all_packages]
    for future in tqdm(as_completed(futures), total=len(futures)):
        # re-raise any worker exceptions
        future.result()

snapshot = {
    "generated_at": datetime.utcnow().isoformat() + "Z",
    "sections": config,
}

with open("generated.yml", "w") as generated_output:
    dump(snapshot, generated_output)
