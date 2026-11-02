// Yale Assure Lock 2 whose Zigbee module reports modelID "YRD410-PB" (hyphen) and
// manufacturerName "Yale:8104:05D1". zigbee-herdsman-converters only matches the
// spaced "YRD410 PB" / "YRD410 TS", so Zigbee2MQTT treats this lock as unsupported
// (no lock state, no PIN codes). Reuse the upstream YRD410-BLE definition and add
// the hyphenated modelID. Remove once upstream matches it (Koenkk/zigbee-herdsman-
// converters#13513) and the two differences below are no longer needed.
//
// Differences from upstream, both measured on this lock 2026-10-16:
// 1. configure: the module answers closuresDoorLock configReport(lockState) with
//    UNSUP_GENERAL_COMMAND, which aborted upstream's configure before battery
//    reporting was set up. Each step is now tried on its own.
// 2. With no lockState reporting, a successful lock/unlock never updated `state`.
//    Operation event notifications now set state/lock_state too, as Yale's own
//    c4_lock_operation_event does for the Control4 module.
import yale from "zigbee-herdsman-converters/devices/yale";
import * as reporting from "zigbee-herdsman-converters/lib/reporting";

const base = yale.definitions.find((d) => d.model === "YRD410-BLE");
if (!base || base.extend?.length !== 1 || !base.extend[0].fromZigbee) {
    throw new Error("upstream Yale YRD410-BLE definition not found or changed shape");
}
const upstream = base.extend[0];

const LOCKED = new Set([1, 7, 8, 10, 11, 13]); // lock, one_touch, key, auto, schedule, manual
const UNLOCKED = new Set([2, 9, 12, 14]); // unlock, key, schedule, manual

const stateFromOperationEvent = {
    cluster: "closuresDoorLock",
    type: "commandOperationEventNotification",
    convert: (model, msg) => {
        const code = msg.data.opereventcode;
        if (LOCKED.has(code)) return {state: "LOCK", lock_state: "locked"};
        if (UNLOCKED.has(code)) return {state: "UNLOCK", lock_state: "unlocked"};
    },
};

const tryStep = async (step) => {
    try {
        await step();
    } catch {
        // This module refuses some reporting requests; keep configuring the rest.
    }
};

export default {
    ...base,
    zigbeeModel: ["YRD410-PB"],
    model: "YRD410-PB",
    description: "Assure lock 2 (module reports YRD410-PB)",
    extend: [
        {
            ...upstream,
            fromZigbee: [...upstream.fromZigbee, stateFromOperationEvent],
            configure: [
                async (device, coordinatorEndpoint) => {
                    const endpoint = device.getEndpoint(1);
                    await reporting.bind(endpoint, coordinatorEndpoint, ["closuresDoorLock", "genPowerCfg"]);
                    await tryStep(() => reporting.lockState(endpoint));
                    await tryStep(() => reporting.batteryPercentageRemaining(endpoint));
                    await tryStep(() => reporting.batteryAlarmState(endpoint));
                },
            ],
        },
    ],
};
