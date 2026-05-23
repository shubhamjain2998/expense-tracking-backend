# API examples

Ready-to-run requests for exploring the API by hand.

## Using `api.http`

The `.http` format is recognised by:

- **VS Code** — install the [REST Client](https://marketplace.visualstudio.com/items?itemName=humao.rest-client) extension. Open `api.http` and click the "Send Request" link that appears above each `###`-separated block.
- **JetBrains IDEs** (PyCharm, IntelliJ, WebStorm) — `.http` files are supported out of the box. Click the green ▶ arrow next to any request.

The first request you run should be `Register` or `Login` — it sets a `token` cookie that subsequent requests in the same file reuse automatically.

## Workflow

1. Start the API: `python server.py`
2. Open [`api.http`](api.http).
3. Register a user (or log in if you already have one).
4. Create a category, then a budget, then a person.
5. Add a raw transaction manually, then process it.
6. Hit `/dashboard/*` to see analytics.

Replace the placeholder UUIDs (`@cat_id`, `@raw_id`, `@person_id`) with values returned by the earlier requests.

## curl fallback

If you prefer the terminal, the same calls work as curl one-liners. For example:

```bash
# Login (saves cookie to a jar)
curl -c cookies.txt -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"correct-horse-battery-staple"}'

# Subsequent calls reuse the cookie
curl -b cookies.txt http://localhost:8000/auth/me
curl -b cookies.txt http://localhost:8000/categories
```

For interactive exploration, the auto-generated Swagger UI at <http://localhost:8000/docs> lets you "Try it out" against the same endpoints without leaving the browser.
