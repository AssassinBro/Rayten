#!/usr/bin/env python3

#
# Sends updates to a Discord webhook for new changelog entries since the last GitHub Actions publish run.
# Automatically figures out the last run and changelog contents with the GitHub API.
#

import io
import itertools
import os
import requests
import yaml
import time

GITHUB_API_URL    = os.environ.get("GITHUB_API_URL", "https://api.github.com")
GITHUB_REPOSITORY = os.environ["GITHUB_REPOSITORY"]
GITHUB_RUN        = os.environ["GITHUB_RUN_ID"]
GITHUB_TOKEN      = os.environ["GITHUB_TOKEN"]

# https://discord.com/developers/docs/resources/webhook
DISCORD_SPLIT_LIMIT = 2000
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

CHANGELOG_FILES = ["Resources/Changelog/Changelog.yml", "Resources/Changelog/ChangelogSyndie.yml", "Resources/Changelog/ChangelogVanilla.yml"] # Corvax-vanilla-MultiChangelog

TYPES_TO_EMOJI = {
    "Fix":    "🐛",
    "Add":    "✨", # Corvax: Use gitmoji 💥
    "Remove": "❌",
    "Tweak":  "⚒️"
}

ChangelogEntry = dict[str, Any]

def main():
    if not DISCORD_WEBHOOK_URL:
        return

    session = requests.Session()
    session.headers["Authorization"]        = f"Bearer {GITHUB_TOKEN}"
    session.headers["Accept"]               = "Accept: application/vnd.github+json"
    session.headers["X-GitHub-Api-Version"] = "2022-11-28"

    most_recent = get_most_recent_workflow(session)
    last_sha = most_recent['head_commit']['id']
    print(f"Last successful publish job was {most_recent['id']}: {last_sha}")

    # Corvax-MultiChangelog-Start
    for changelog_file in CHANGELOG_FILES:
        last_changelog = yaml.safe_load(get_last_changelog(session, last_sha, changelog_file))
        with open(changelog_file, "r") as f:
            cur_changelog = yaml.safe_load(f)

        diff = diff_changelog(last_changelog, cur_changelog)
        send_to_discord(diff)
    # Corvax-MultiChangelog-End


def get_most_recent_workflow(sess: requests.Session) -> Any:
    workflow_run = get_current_run(sess)
    past_runs = get_past_runs(sess, workflow_run)
    for run in past_runs['workflow_runs']:
        # First past successful run that isn't our current run.
        if run["id"] == workflow_run["id"]:
            continue

        return run


def get_current_run(sess: requests.Session) -> Any:
    resp = sess.get(f"{GITHUB_API_URL}/repos/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN}")
    resp.raise_for_status()
    return resp.json()


def get_past_runs(sess: requests.Session, current_run: Any) -> Any:
    """
    Get all successful workflow runs before our current one.
    """
    params = {
        "status": "success",
        "created": f"<={current_run['created_at']}"
    }
    resp = sess.get(f"{current_run['workflow_url']}/runs", params=params)
    resp.raise_for_status()
    return resp.json()


def get_last_changelog(sess: requests.Session, sha: str, changelog_file: str) -> str:
    """
    Use GitHub API to get the previous version of the changelog YAML (Actions builds are fetched with a shallow clone)
    """
    params = {
        "ref": sha,
    }
    headers = {
        "Accept": "application/vnd.github.raw"
    }

    resp = sess.get(f"{GITHUB_API_URL}/repos/{GITHUB_REPOSITORY}/contents/{changelog_file}", headers=headers, params=params)
    resp.raise_for_status()
    return resp.text


def diff_changelog(old: dict[str, Any], cur: dict[str, Any]) -> Iterable[ChangelogEntry]:
    """
    Find all new entries not present in the previous publish.
    """
    old_entry_ids = {e["id"] for e in old["Entries"]}
    return (e for e in cur["Entries"] if e["id"] not in old_entry_ids)


def get_discord_body(content: str):
    return {
            "content": content,
            # Do not allow any mentions.
            "allowed_mentions": {
                "parse": []
            },
            # SUPPRESS_EMBEDS
            "flags": 1 << 2
        }


def send_discord(content: str):
    body = get_discord_body(content)
    retry_attempt = 0

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=body, timeout=10)
        while response.status_code == 429:
            retry_attempt += 1
            if retry_attempt > 20:
                print("Too many retries on a single request despite following retry_after header... giving up")
                exit(1)
            retry_after = response.json().get("retry_after", 5)
            print(f"Rate limited, retrying after {retry_after} seconds")
            time.sleep(retry_after)
            response = requests.post(DISCORD_WEBHOOK_URL, json=body, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Failed to send message: {e}")
        exit(1)

# Rayten-Localization-Start
def translate_to_russian(text: str) -> str:
    url = "https://libretranslate.com/translate"
    payload = {
        "q": text,
        "source": "auto",
        "target": "ru",
        "format": "text",
        "api_key": ""
    }

    headers = {
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        response.raise_for_status()
        return response.json().get("translatedText", text)
    except Exception as e:
        print(f"Translation failed: {e}")
        return text
# Rayten-Localization-End


def send_to_discord(entries: Iterable[ChangelogEntry]) -> None:
    if not DISCORD_WEBHOOK_URL:
        print(f"No discord webhook URL found, skipping discord send")
        return

    message_content = io.StringIO()
    # We need to manually split messages to avoid discord's character limit
    # With that being said this isn't entirely robust
    # e.g. a sufficiently large CL breaks it, but that's a future problem

    for name, group in itertools.groupby(entries, lambda x: x["author"]):
        # Need to split text to avoid discord character limit
        group_content = io.StringIO()
        group_content.write(f"**{name}** обновил(а):\n")

        for entry in group:
            for change in entry["changes"]:
                emoji = TYPES_TO_EMOJI.get(change['type'], "❓")
                message = change['message']
                url = entry.get("url")

                # Rayten-Localization-Start
                message = translate_to_russian(message)
                # Rayten-Localization-End

                if url and url.strip():
                    group_content.write(f"{emoji} - {message} [PR]({url}) \n")
                else:
                    group_content.write(f"{emoji} - {message}\n")
        group_content.write(f"\n")  # Corvax: Better formatting

        group_text = group_content.getvalue()
        message_text = message_content.getvalue()
        message_length = len(message_text)
        group_length = len(group_text)

        # If adding the text would bring it over the group limit then send the message and start a new one
        if message_length + group_length >= DISCORD_SPLIT_LIMIT:
            print("Split changelog  and sending to discord")
            send_discord(message_text)

            # Reset the message
            message_content = io.StringIO()

        # Flush the group to the message
        message_content.write(group_text)

    # Clean up anything remaining
    message_text = message_content.getvalue()
    if len(message_text) > 0:
        print("Sending final changelog to discord")
        message_content.seek(0)  # Corvax
        for chunk in iter(lambda: message_content.read(2000), ''): # Corvax: Split big changelogs messages
            send_discord(chunk)


main()
