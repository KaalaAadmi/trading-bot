## Trading Bot

To Commit changes:

```sh
pre-commit run --all-files
git add -A
git commit -m "<conventional commit>"
```

To run use inside docker folder:

```sh
docker compose --env-file ../.env up -d
```

OR

```sh
docker compose -f docker/docker-compose.yaml up -d
```
