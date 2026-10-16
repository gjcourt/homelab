// Yale Assure Lock 2 whose Zigbee module reports modelID "YRD410-PB" (hyphen) and
// manufacturerName "Yale:8104:05D1". zigbee-herdsman-converters only matches the
// spaced "YRD410 PB" / "YRD410 TS", so Zigbee2MQTT treats this lock as unsupported
// (no lock state, no PIN codes). Reuse the upstream YRD410-BLE definition
// unchanged and only add the hyphenated modelID. Remove once upstream matches it.
import yale from "zigbee-herdsman-converters/devices/yale";

const base = yale.definitions.find((d) => d.model === "YRD410-BLE");
if (!base) throw new Error("upstream Yale YRD410-BLE definition not found");

export default {
    ...base,
    zigbeeModel: ["YRD410-PB"],
    model: "YRD410-PB",
    description: "Assure lock 2 (module reports YRD410-PB)",
};
