# Limbo + Manus Tunnel

Limbo can connect an existing local app to the deployed Limbo Tunnel server without `cloudflared`. The core package stays dependency-free; install the optional signing dependency first:

```bash
python -m pip install -e '.[manus-tunnel]'
```

Create a one-time enrollment token in the Manus Limbo Tunnel admin dashboard, then enroll a client identity. The private key is written with mode `0600` and never sent to Manus:

```python
from limbo import ManusTunnelClient

client, result = ManusTunnelClient.enroll(
    enrollment_url="https://limbotunnel-klzbnko7.manus.space",
    tunnel_url="wss://limbotunnel-klzbnko7.manus.space/tunnel",
    token="ONE_TIME_TOKEN",
    client_id="my-limbo-dev",
    name="My Limbo development app",
    key_path=".limbo/manus-ed25519.pem",
    local_origin="http://127.0.0.1:8000",
)
client.run_forever()
```

The client signs the server challenge with Ed25519, sends heartbeats, reconnects with exponential backoff, forwards registered HTTP hostnames to the local Limbo server, and relays WebSocket frames. Register the public hostname against the enrolled `client_id` in the Manus admin dashboard before sending public traffic.

For production, run the client as a supervised process alongside Limbo. Store the private key outside source control, use `wss://`, and revoke the client from the Manus dashboard when the key or host is no longer trusted.

The client buffers each incoming HTTP request until `http_end` before making the local request. The Manus server still enforces request/response size limits and cancellation; true chunk-by-chunk local request streaming can be added later if a Limbo application needs it.
