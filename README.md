# loadshaper

How to run

On each VM:

```shell
mkdir -p loadshaper
# put the three files in place
docker compose up -d --build
# or: podman compose up -d
```

Then watch:
```shell
docker logs -f loadshaper
# or: podman logs -f loadshaper
```