# Usage reporters

Copy the file for your app's language into the app repo and call `report(...)`
after each Anthropic API call. Canonical copies live here — fix bugs and pricing
changes here first, then re-copy.

## Contract

Never raises, never blocks. A reporting failure loses one data point; it must
never affect the calling app.

## Railway variables required per app

| Variable | Value |
|---|---|
| `COACH_USAGE_URL` | `https://coach-web-production-1f04.up.railway.app/api/usage` |
| `COACH_USAGE_TOKEN` | the value of `COACH_USAGE_TOKEN` on the coach-web service |

## Python

```python
from usage import report

response = client.messages.create(model=MODEL, ...)
report("b2b-ai-news", MODEL, response.usage)
```

## Node

```javascript
import { report } from "./usage.js";

const response = await client.messages.create({ model: MODEL, ... });
report("b2b-ai-news", MODEL, response.usage);
```

The first argument must be the app's `name` from the repo-root `apps.yaml`.
An unregistered name is rejected with a 400, so a typo surfaces immediately
rather than silently vanishing.
