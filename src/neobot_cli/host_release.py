"""首个开发版本固定到已验收宿主源码；宿主正式发版后再更新发布配方。"""

HOST_REPOSITORY = "https://github.com/SuperQuail/NeoBot.git"
HOST_REVISION = "d1fee6e56ce768b278973c5341eaf1c786e3ba01"
HOST_PACKAGES = {
    "neobot-app": "app",
    **{
        f"neobot-{name}": f"packages/{name}"
        for name in ("adapter", "chat", "contracts", "memory", "modloader", "storage")
    },
}
