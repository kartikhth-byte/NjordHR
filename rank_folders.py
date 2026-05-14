from pathlib import Path


def rank_folder_slug(rank):
    return str(rank or "").strip().replace(" ", "_").replace("/", "-")


def rank_folder_path(download_root, rank):
    return Path(download_root) / rank_folder_slug(rank)
