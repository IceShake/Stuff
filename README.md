# Personal FreeTheAI relay for Render

A small, fixed-upstream reverse proxy for **your own** FreeTheAI API key. It forwards `/v1/*` requests to `https://api.freetheai.org`, including streaming responses. It does not store API keys or request bodies, and it is not an arbitrary-URL proxy.

## Important limits and terms

- Use this for your own account/key; do not publish the endpoint as a shared or pooled-key service.
- The relay forwards the API key supplied by the client. **Do not add your FreeTheAI key to Render environment variables or commit it to GitHub.** Put your own key in Lorebary's API-key field.
- FreeTheAI's terms prohibit sharing or pooling API access/keys unless expressly allowed. Review the current [Terms](https://freetheai.org/terms) before using a relay.
- FreeTheAI's API docs note that requests relayed through a Cloudflare Worker can be rejected with 403. This service runs on Render, not as a Cloudflare Worker, but FreeTheAI can still apply key, IP, client, or network restrictions; a Render relay is not guaranteed to work. If FreeTheAI rejects the request, use its supported direct setup or ask its support rather than trying to evade the restriction.

## Deploy on Render

1. Put these files in a GitHub repository. Keep API keys out of the repository.
2. In Render, choose **New → Blueprint**, connect the repository, and deploy the `render.yaml` Blueprint.
3. After deployment, copy the service's public URL, for example `https://your-service.onrender.com`.
4. In Lorebary, set the OpenAI-compatible base URL to:

   ```text
   https://your-service.onrender.com/v1
   ```

5. Enter **your own FreeTheAI API key** in Lorebary's API-key field and choose a model ID allowed for that key.

The proxy keeps the incoming `/v1/...` path, so `/v1/chat/completions`, `/v1/models`, `/v1/responses`, and other `/v1` routes are forwarded to FreeTheAI.

## Optional proxy authentication

The service requires an API-key header (`Authorization`, `x-api-key`, or `api-key`) and forwards it upstream. If Lorebary supports an additional custom header, you can add a second layer of protection:

1. Set a long random `PROXY_SHARED_KEY` environment variable on the Render service.
2. Configure Lorebary to send the matching value in an `X-Proxy-Key` header.

When `PROXY_SHARED_KEY` is unset, the relay still only accepts `/v1` paths and requires the caller to supply a FreeTheAI key. Keep the service URL private if Lorebary cannot send a separate proxy-auth header.

## Check the deployment

Open this URL in a browser:

```text
https://your-service.onrender.com/health
```

It should return `{"status":"ok"}`. To check the API (replace the placeholder locally; do not post your key):

```bash
curl https://your-service.onrender.com/v1/models \
  -H "Authorization: Bearer YOUR_FREETHEAI_KEY"
```

A 401/403 response usually means the key, account verification/check-in, model access, or network/IP restrictions need attention. Render's free service may also take a little time to wake after inactivity.

## Configuration

- `UPSTREAM_BASE_URL`: fixed to `https://api.freetheai.org` in the Blueprint. The app deliberately rejects other upstream hosts.
- `PROXY_SHARED_KEY`: optional extra inbound secret; set in Render only if Lorebary can send `X-Proxy-Key`.
- `MAX_BODY_BYTES`: request-body size limit (default 25 MiB).
