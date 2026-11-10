# UBTI Voice Agent - v2

Multi-tenant voice AI agent platform (Retell / ElevenLabs style) on **Azure Voice Live** (gpt-realtime), with
**Vobiz**, **Twilio** and **Azure Communication Services** (ACS numbers + your own **SIP trunk** via Direct Routing),
**Fabric SQL** as the database, and a React dashboard. One container runs both the API and the dashboard.

```
 Caller ──PSTN──> Vobiz / Twilio / ACS (number or SIP trunk via SBC)
                       │  webhook (signed)            │ media WebSocket (signed, bidirectional audio)
                       ▼                              ▼
              ┌──────────────── Azure Container Apps (this repo) ────────────────┐
              │ FastAPI: answer webhooks · CallSession bridge · REST API · UI    │
              │  - silence / voicemail / IVR / DTMF / transfer / recording       │
              │  - functions · knowledge search · variables · live monitor       │
              └──────┬───────────────┬────────────────┬─────────────┬────────────┘
                     │ WebSocket     │ SQL (ODBC)     │ Blob        │ Redis (optional, multi-replica)
                     ▼               ▼                ▼             ▼
              Azure Voice Live   Fabric SQL DB   Recordings    pub/sub + live registry
              (gpt-realtime)     (+ Azure OpenAI: post-call analysis, embeddings)
```

---

## 1. What's new in v2

| Area | What you get |
|---|---|
| **Bug fixes** | "Start batch" and "Approve voice" no longer crash (worker-thread event-loop bug). A paused agent no longer answers calls. "Who speaks first" setting replaces the misleading empty-first-message hint. Transfer works on Vobiz (and no longer gets hung up on Twilio). Functions tab added. |
| **Azure SIP (ACS)** | ACS Call Automation: ACS phone numbers **and Direct Routing SIP trunks**, inbound via Event Grid, outbound calls, bidirectional 24 kHz audio, DTMF, cold + warm transfer, ring timeout. |
| **Call handling** | End call on silence, "are you still there?" reminders, pause before speaking, voicemail detection (carrier AMD on Twilio/Vobiz + AI detection) with hang up or leave-a-message, IVR hang-up, call-screening message, ring duration, max duration. |
| **Dynamic variables** | `{{customer_name}}`, `{{due_amount}}`... in prompt, first message, voicemail text and functions. Filled from the outbound API, batch CSV columns or agent defaults. System variables: `current_time`, `current_date`, `day_of_week`, `user_number`, `agent_number`, ... |
| **Functions** | Dashboard tab: method, URL with variables, headers, parameters, wait message, timeout, **retries**, test button. |
| **Transfers** | Cold transfer on Vobiz / Twilio / ACS. Warm transfer: Twilio & Vobiz whisper an AI briefing to the human first; ACS adds the human, the AI introduces the caller, then leaves. |
| **Recordings** | Two-channel recording (caller left / agent right) made inside the bridge for every provider, MP3, stored in Azure Blob, player in call details. |
| **Post-call analysis** | Built-ins (summary, sentiment, outcome, language, call successful, reached voicemail) + **custom fields** (string/number/boolean/enum). Re-run analysis. Webhooks `call.started`, `call.ended`, `call.analyzed` (HMAC signed, retried). CSV export includes custom fields. |
| **Security** | Every provider URL signed (outbound `?sig=`, inbound per-tenant key `?k=`, media WebSocket signature in path). Weak `JWT_SECRET` refused. Roles enforced (viewer read-only, member builds, admin manages numbers/keys/team/privacy). SSRF guard on functions, knowledge URLs and webhooks. |
| **Knowledge base** | No more silent cut-off at 15k: small KBs go in the prompt, large ones are chunked and searched during the call (`search_knowledge` tool) - Azure OpenAI embeddings or BM25 keyword fallback. Re-fetch URL, re-index. |
| **Agent versions** | Edits are saved as a draft; **Publish** creates a version used by live calls; view, restore, roll back. Browser test uses the draft. |
| **Cost** | Real per-call cost from Voice Live token usage x model rates + telephony minutes x carrier rate (rates configurable). |
| **Boosted keywords** | Phrase list for names/brands sent to Voice Live transcription. |
| **Keypad (DTMF)** | Timeout, termination key, digit limit (Twilio, ACS, browser test keypad). |
| **Batch calls** | Database-driven scheduler (restart-safe, multi-replica-safe): calling windows + days + timezone, retries for no-answer/busy/voicemail, stop, retry-failed, CSV columns become variables, up to 20 parallel. |
| **Live monitoring** | Live Calls page: all calls in progress, live transcript, end call (works across replicas). |
| **Privacy** | PII redaction of transcripts/summaries/webhooks (phone, email, card, Emirates ID, Aadhaar, PAN, IBAN, spoken numbers) + data retention period (auto-delete). |
| **Multi-replica** | Set `REDIS_URL` and run several replicas: control/monitor messages, live-call registry, transfer state and the scheduler lock go through Redis. |

---

## 2. Why Azure Container Apps (not Firebase Functions)

Every call - Vobiz, Twilio, ACS and the browser test - is a **WebSocket that stays open for the whole call** and streams
audio both ways between the phone line and Voice Live. Cloud Functions for Firebase only answer one HTTP request at a
time (no WebSocket server), throttle work after the response, and cannot install the Microsoft ODBC driver that Fabric
SQL needs. So the backend runs as a container. Container Apps sits next to Voice Live, ACS and Fabric (lowest audio
latency) and receives ACS Event Grid events natively. The dashboard is served by the same container, so there is
nothing to host on Firebase (you can still put the `frontend/dist` folder on Firebase Hosting if you prefer -
set `VITE_API_URL` to the Container Apps URL when building).

---

## 3. Run locally

Prereqs: Python 3.12, Node 20+, [ODBC Driver 18](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server), `az login` (for Fabric), ffmpeg (optional, MP3 recordings), a tunnel (ngrok / Cloudflare) for phone calls.

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # fill in JWT_SECRET, Fabric, Voice Live, Azure OpenAI, PUBLIC_BASE_URL (tunnel URL)
uvicorn app.main:app --reload --port 8000
# first start creates/updates all tables (AUTO_MIGRATE=true) and publishes v1 of existing agents

# dashboard (second terminal)
cd frontend
npm install
npm run dev                   # http://localhost:5173  (talks to http://localhost:8000)
```

Tests (no Azure needed - Fabric, Voice Live and the phone line are simulated):
```bash
cd backend && pip install pytest && python -m pytest -q
```

---

## 4. Deploy to Azure Container Apps

Use the **same region as your Voice Live (Foundry) resource** for the lowest latency. All commands run in
Azure Cloud Shell or any terminal with the Azure CLI - the image is built in Azure, you do not need Docker locally.

```bash
az login
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.EventGrid

RG=ubti-voice-rg
LOC=swedencentral            # <- region of your Voice Live resource
ACR=ubtivoiceacr$RANDOM      # globally unique, lowercase
ST=ubtivoicest$RANDOM        # globally unique, lowercase
APP=ubti-voice

az group create -n $RG -l $LOC

# 1) build the image in Azure (run from the repo root, where the Dockerfile is)
az acr create -n $ACR -g $RG --sku Basic
az acr build -r $ACR -t ubti-voice:v2 .

# 2) storage for recordings + voice-clone audio
az storage account create -n $ST -g $RG -l $LOC --sku Standard_LRS --min-tls-version TLS1_2
STORAGE_CS=$(az storage account show-connection-string -n $ST -g $RG -o tsv)

# 3) Container Apps environment
az containerapp env create -n ubti-env -g $RG -l $LOC

# 4) the app  (fill the values in <...>; secrets are stored encrypted in Container Apps)
JWT=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
az containerapp create -n $APP -g $RG --environment ubti-env \
  --image $ACR.azurecr.io/ubti-voice:v2 --registry-server $ACR.azurecr.io --registry-identity system \
  --system-assigned \
  --ingress external --target-port 8000 --transport http \
  --min-replicas 1 --max-replicas 1 --cpu 1.0 --memory 2.0Gi \
  --secrets jwt="$JWT" vl-key="<voice-live-key>" aoai-key="<azure-openai-key>" speech-key="<speech-key>" \
            storage="$STORAGE_CS" admin-pw="<admin-password>" vobiz-token="<vobiz-token-or-x>" acs="<acs-connection-string-or-x>" \
  --env-vars JWT_SECRET=secretref:jwt OLD_JWT_SECRET="<your-old-JWT_SECRET-if-any>" \
    FABRIC_SQL_SERVER="<xxx.database.fabric.microsoft.com>" FABRIC_SQL_DATABASE="<db-name>" FABRIC_AUTH_MODE=msi \
    AZURE_VOICELIVE_ENDPOINT="<https://xxx.services.ai.azure.com>" AZURE_VOICELIVE_API_KEY=secretref:vl-key \
    AZURE_OPENAI_ENDPOINT="<https://xxx.openai.azure.com>" AZURE_OPENAI_API_KEY=secretref:aoai-key \
    AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini AZURE_OPENAI_EMBED_DEPLOYMENT=text-embedding-3-small \
    AZURE_SPEECH_KEY=secretref:speech-key AZURE_SPEECH_REGION="<region>" \
    AZURE_STORAGE_CONNECTION_STRING=secretref:storage \
    VOBIZ_AUTH_ID="<vobiz-auth-id>" VOBIZ_AUTH_TOKEN=secretref:vobiz-token ACS_CONNECTION_STRING=secretref:acs \
    ADMIN_EMAIL="admin@ubtiinc.com" ADMIN_PASSWORD=secretref:admin-pw \
    ALLOW_UNSIGNED_WEBHOOKS=true
```
(`--registry-identity system` lets the app pull from your registry with its managed identity. If your CLI version does not
support it: `az acr update -n $ACR --admin-enabled true` and pass `--registry-username/--registry-password` instead.)

```bash
# 5) tell the app its public URL (used for webhooks and media WebSockets)
FQDN=$(az containerapp show -n $APP -g $RG --query properties.configuration.ingress.fqdn -o tsv)
az containerapp update -n $APP -g $RG --set-env-vars PUBLIC_BASE_URL=https://$FQDN FRONTEND_ORIGIN=https://$FQDN
echo "Dashboard: https://$FQDN"
```

**6) Give the app access to Fabric SQL.** The app signs in to Fabric with its managed identity (`FABRIC_AUTH_MODE=msi`).
In Fabric: open the workspace > **Manage access** > **Add people or groups** > search for the app name (`ubti-voice`) >
role **Contributor**. Or run in the Fabric SQL database's query editor:
```sql
CREATE USER [ubti-voice] FROM EXTERNAL PROVIDER;
ALTER ROLE db_owner ADD MEMBER [ubti-voice];   -- db_owner because AUTO_MIGRATE adds tables/columns
```
Prefer your existing service principal? Use `FABRIC_AUTH_MODE=sp` with `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`.

Then: `az containerapp logs show -n $APP -g $RG --follow` - you should see `migration done` and `UBTI Voice Agent ready`.
Open the dashboard and sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

**Deploy a new version**
```bash
az acr build -r $ACR -t ubti-voice:v2.1 .
az containerapp update -n $APP -g $RG --image $ACR.azurecr.io/ubti-voice:v2.1
```
A new revision starts and the old one stops - calls on the old replica end, so deploy in quiet hours.

**Custom domain** (recommended for clients): `az containerapp hostname add` + `az containerapp hostname bind --validation-method CNAME`, then update `PUBLIC_BASE_URL`.

**Scaling beyond one replica**: create an Azure Managed Redis / Azure Cache for Redis, set `REDIS_URL=rediss://:<key>@<host>:6380/0`,
then `az containerapp update -n $APP -g $RG --min-replicas 2 --max-replicas 6 --scale-rule-name ws --scale-rule-type http --scale-rule-http-concurrency 40`.
Each live call is one WebSocket (one concurrent request). Keep `--min-replicas` >= 1 so the first call never waits for a cold start.

---

## 5. Connect phone numbers

All webhook URLs are shown in the dashboard: **Phone Numbers > Setup** (owner/admin only). They contain your account key - keep them private.

### Vobiz (India)
Console > Voice > Applications: Answer URL = the dashboard's *Answer URL* (POST), Hangup URL = *Status URL*. Attach the app to the number.
Outbound, batch, ring timeout (`hangup_on_ring`), answering-machine detection and transfers use the REST API with the saved credentials.

### Twilio
Number > Voice: "A call comes in" = *Answer URL* (POST), "Call status changes" = *Status URL*. Outbound calls use
`MachineDetection` (+ `DetectMessageEnd` when leaving voicemails) and `Timeout` (ring duration).

### Azure Communication Services - "Azure SIP"
ACS Call Automation is Microsoft's documented way to connect phone calls to Voice Live. It covers two cases with the same code:
* **ACS phone numbers** bought in Azure (availability depends on the country - check *Phone numbers > Get* in the portal), and
* **Direct Routing**: connect a carrier SIP trunk (e.g. your Kaleyra / local carrier trunk) through a **Microsoft-certified SBC**
  (AudioCodes, Ribbon, Oracle, ... or an SBC-as-a-service provider). This is how you keep your own carrier and numbers.

```bash
az communication create -n ubti-acs -g $RG --location global --data-location UAE      # or India, Europe, ...
az communication list-key -n ubti-acs -g $RG --query primaryConnectionString -o tsv    # -> ACS_CONNECTION_STRING
```
1. Put the connection string in `ACS_CONNECTION_STRING` (platform-wide) **or** per tenant in the dashboard: Phone Numbers > Azure (ACS) card > key icon.
2. **Numbers:** Portal > ACS resource > *Phone numbers* > Get, **or** *Direct routing*:
   - *Domains*: add and verify the domain of your SBC (TXT record),
   - *Session Border Controllers*: add the SBC FQDN (TLS 5061, public CA certificate on the SBC),
   - *Voice routes*: number pattern (e.g. `^\+971\d+$`) -> your SBC.
   - On the SBC: trunk to the carrier on one side, to `sip.pstnhub.microsoft.com` (+ `sip2`/`sip3`) on the other, SRTP.
3. **Incoming calls:** subscribe the ACS resource to Event Grid with the *Event Grid webhook URL* from Phone Numbers > Setup:
   ```bash
   ACS_ID=$(az communication show -n ubti-acs -g $RG --query id -o tsv)
   az eventgrid event-subscription create --name ubti-incoming-call --source-resource-id $ACS_ID \
     --endpoint "https://<your-host>/telephony/acs/incoming?k=<key>" \
     --included-event-types Microsoft.Communication.IncomingCall
   ```
   (The validation handshake is answered automatically.)
4. In the dashboard add the number with provider **Azure (ACS)** (+E.164, for Direct Routing the number your trunk delivers) and assign an agent.

Per-call events (connected, disconnected, transfer, participant joined) come back on a signed callback URL that is set
automatically for every call - nothing to configure.

---

## 6. Upgrading from v1 (checklist)

1. **Secrets**: set a strong `JWT_SECRET` (the app refuses the sample one outside `DEV_MODE`). Put the old value in
   `OLD_JWT_SECRET` so saved Vobiz/Twilio tokens can still be decrypted (or re-enter them). Everyone signs in again.
2. **Database**: first start runs the migration (new tables `agent_versions`, `knowledge_chunks`, new columns). The
   identity needs ALTER rights - or run `python -m app.migrate` once with an admin login. Every existing agent is published as **v1**.
3. **Webhook URLs changed** (now signed). Deploy with `ALLOW_UNSIGNED_WEBHOOKS=true`, update each Vobiz/Twilio number
   with the URLs from Phone Numbers > Setup, test a call, then set `ALLOW_UNSIGNED_WEBHOOKS=false`.
4. **Publish after editing**: agent edits are drafts now - press **Publish** for live calls to use them.
5. **Recordings / voice clones** need `AZURE_STORAGE_CONNECTION_STRING` on Container Apps (local disk is temporary).
6. Existing knowledge documents work immediately; press **Re-index** on the Knowledge page after enabling embeddings.
7. Roles are enforced now: give team members `member` (builds agents) or `admin` (numbers, keys, team) as needed.

---

## 7. API quick reference

```bash
# Outbound call with variables + metadata (API key from Developers page)
curl -X POST https://<host>/api/calls/outbound -H "X-API-Key: ubti_xxx" -H "Content-Type: application/json" \
  -d '{"agent_id":"<id>","phone_number_id":"<id>","to":"+971501234567",
       "variables":{"customer_name":"Aisha","due_amount":"1200 AED"},"metadata":{"crm_id":"L-1029"}}'

# End a live call
curl -X POST https://<host>/api/calls/<call_id>/end -H "X-API-Key: ubti_xxx"
```

Webhooks: `POST` JSON `{"event": "call.analyzed", "data": {...call, transcript, analysis: {custom: {...}}}}` with headers
`X-UBTI-Event` and `X-UBTI-Signature` = hex HMAC-SHA256 of the raw body with the endpoint's signing secret.
```python
import hmac, hashlib
ok = hmac.compare_digest(hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest(), request.headers["X-UBTI-Signature"])
```
Interactive API docs: `https://<host>/docs`.

---

## 8. Good to know

* **Run one uvicorn worker per container** (the Dockerfile does). Scale with replicas + `REDIS_URL`, not workers.
* **Keypad (DTMF)**: Twilio and ACS send keypad presses in the media stream; Vobiz's stream currently does not (the code handles it if Vobiz adds a `dtmf` event).
* **Voicemail on ACS**: ACS has no carrier answering-machine detection, so on ACS the agent's own detection is used.
* **Warm transfer on Vobiz** uses `<Dial confirmSound>` to play the briefing - verify the behaviour on your Vobiz account.
* **Pricing defaults are placeholders**: put your Azure Voice Live / carrier rates in `PRICING_JSON` for accurate per-call costs.
* **Boosted keywords** use Voice Live's `phrase_list` (azure-speech transcription). If Voice Live rejects the settings, the bridge automatically retries with reduced settings (see logs: "settings level").
* Recordings cover the AI part of the call; after a transfer the human conversation is not recorded.
