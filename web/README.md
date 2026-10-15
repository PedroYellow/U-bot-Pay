# Web (u.bot look & feel)

Design system in `ubot.css`: Poppins, deep-indigo left panel with the glowing voice orb, light panel on the right, `#1F5AF0` accent — mirrors u.bot. `index.html` = main app, `agent.html` = DataAgent page (served by `dataagent.py` on agent.ubotpay.com).
Static page served by nginx at https://www.ubotpay.com. Before deploying, replace `__UBOT_API_KEY__` and `__AGENT_ID__`:

    sed -e "s|__UBOT_API_KEY__|$UBOT_API_KEY|" -e "s|__AGENT_ID__|$AGENT_ID|" web/index.html > /var/www/ubotpay/index.html
    sed -e "s|__UBOT_API_KEY__|$UBOT_API_KEY|" web/agent.html > /var/www/ubotpay/agent.html
    cp web/ubot.css /var/www/ubotpay/

Voice uses the ElevenLabs Conversational AI SDK (`@elevenlabs/client`) over WebRTC; the agent calls `POST /turn` as a webhook tool.
