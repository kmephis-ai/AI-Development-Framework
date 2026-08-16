function evaluateEdgeState(state) {
  const actions = [];
  if (state.leak === true) {
    actions.push({ channel: "water_main", value: false, reason: "leak" });
  }
  if (Number(state.temperature_c) < 5) {
    actions.push({ channel: "freeze_protection", value: true, reason: "low_temperature" });
  }
  if (state.away === true && state.door_open === true) {
    actions.push({ channel: "security_notice", value: true, reason: "door_open_away" });
  }
  return actions;
}
