# Деплой v2 на psy.cookiteasy.food

Выкатываем перевыделенный корпус (голос Ани) + новый код (reranker, цитаты,
`/retrieve`) на прод. Прод — отдельный сервер (`/home/deploy/psy-helper`), своя БД,
nginx → `127.0.0.1:8501`.

**Что меняется:** код (retrieval/prompts/embed_segments/api), миграция 007, и —
главное — **данные**: корпус concepts (2418, с quotes/salience/классификацией) +
переэмбедженные segment_embeddings.

> ⚠️ Перед дампом БД дождись окончания классификации (`docker logs psy-classify-local`),
> иначе topics/hunt_stages уедут пустыми.

## 0. Предусловия
- Локально классификация завершена (фильтры в `/retrieve` работают).
- Доступ к серверу по SSH как `deploy`.
- На сервере есть `.env` (ANTHROPIC_API_KEY, STREAMLIT_PASSWORD, STREAMLIT_COOKIE_KEY, POSTGRES_PASSWORD).

## 1. Локально — закоммитить + запушить код
```bash
cd ~/Desktop/dev/psy-helper
git add -A
git commit -m "feat(corpus): перевыделение v2 голосом Ани + reranker + /retrieve API"
git push origin main
```

## 2. Локально — дамп БД (корпус)
Полный дамп (надёжнее всего: UUID-ссылки concepts→clean_segments консистентны):
```bash
docker exec psy-helper-postgres-1 pg_dump -U psy -d psy_helper -Fc -f /tmp/psy_v2.dump
docker cp psy-helper-postgres-1:/tmp/psy_v2.dump ./psy_v2.dump   # ~157 МБ
scp ./psy_v2.dump deploy@<server>:/home/deploy/psy-helper/
```
> Прод-БД будет заменена. Auth у прода через env (не в БД), так что юзер-данных в БД нет.
> Если на проде есть уникальное (напр. свежий voice_document) — выгрузи отдельно заранее.

## 3. На сервере — выкатка
```bash
cd /home/deploy/psy-helper
git pull origin main
docker compose -f docker-compose.prod.yml build           # подтянет fastapi/uvicorn

# Восстановить корпус (миграция 007 уже внутри дампа — отдельно применять не нужно)
docker compose -f docker-compose.prod.yml up -d postgres
docker cp psy_v2.dump $(docker compose -f docker-compose.prod.yml ps -q postgres):/tmp/
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U psy -d psy_helper --clean --if-exists /tmp/psy_v2.dump

# Поднять UI (новый код) + retrieval-сервис
docker compose -f docker-compose.prod.yml up -d ui retrieval
```

## 4. Проверка
```bash
# UI: открыть https://psy.cookiteasy.food — поиск выдаёт концепты с цитатами Ани.
docker compose -f docker-compose.prod.yml logs -f ui | head

# Retrieval (с сервера):
curl -s localhost:8010/health
curl -s localhost:8010/retrieve -H "Content-Type: application/json" \
  -d '{"query":"ревность в паре","k":5}' | python3 -m json.tool
```
Корпус в БД: `concepts` ~2418, у всех quotes; `segment_embeddings` 1073.

## 5. Отдача `/retrieve` заводу
- Завод **на этом же сервере** → ходит на `http://127.0.0.1:8010/retrieve` (или
  `http://retrieval:8000` если в той же docker-сети).
- Завод **снаружи** → НЕ открывать 8010 публично как есть. Добавить в nginx
  protected location с API-ключом, например:
  ```nginx
  location /kb/ {
      if ($http_x_api_key != "<секрет>") { return 401; }
      proxy_pass http://127.0.0.1:8010/;
  }
  ```
  Тогда завод шлёт `POST https://psy.cookiteasy.food/kb/retrieve` с заголовком `X-Api-Key`.

## 6. Откат
- БД: в дампе есть `concepts_v1_backup` (старый корпус) и `concepts_pre_consolidation`.
  Быстрый откат корпуса — `TRUNCATE concepts; INSERT INTO concepts SELECT * FROM concepts_v1_backup;`
  (но эмбеддинги/quotes старого формата — лучше держать дамп прежней БД до выката).
- Код: `git revert` + redeploy.

## RAM
Прод теперь грузит модели в ДВУХ контейнерах (ui + retrieval): e5-large ~2 ГБ ×2 +
bge-reranker ~2.2 ГБ. Убедись, что на сервере хватает RAM (≥8 ГБ комфортно). При нехватке —
запускать `/retrieve` по требованию или вынести на отдельный воркер.
