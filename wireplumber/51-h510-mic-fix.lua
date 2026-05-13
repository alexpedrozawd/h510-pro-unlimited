-- Corrige volume do microfone do H510-PRO ao conectar (USB dongle ou cabo direto).
-- O firmware do headset inicializa Mic,0=0 no ALSA; esta regra força node.volume=1.0
-- que é escrito de volta ao hardware via HW_VOLUME_CTRL.
--
-- Compatível com WirePlumber 0.4.x (testado com 0.4.17).
if type(alsa_monitor) == "table" and type(alsa_monitor.rules) == "table" then
  table.insert(alsa_monitor.rules, {
    matches = {
      { { "node.name", "matches", "alsa_input.usb-XiiSound*" } },
      { { "node.name", "matches", "alsa_input.usb-*H510*" } },
    },
    apply_properties = {
      ["node.volume"] = 1.0,
    },
  })
end
