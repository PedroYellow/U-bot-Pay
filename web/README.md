# Web demo
Static page served by nginx at https://www.ubotpay.com. Before deploying, replace `__UBOT_API_KEY__` and `__AGENT_ID__`:

    sed -e "s|__UBOT_API_KEY__|$UBOT_API_KEY|" -e "s|__AGENT_ID__|$AGENT_ID|" web/index.html > /var/www/ubotpay/index.html

Voice uses the ElevenLabs Conversational AI SDK (`@elevenlabs/client`) over WebRTC; the agent calls `POST /turn` as a webhook tool.
