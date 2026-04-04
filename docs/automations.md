# Example Automations

Ready-to-use HA automations for the Verisure Italy integration.

## Auto force-arm when leaving home

When arming away fails due to open zones, notify about which windows
are open and force-arm automatically. Only triggers for `armed_away`
(leaving home) — for `armed_home` (night mode) the dashboard Force
Arm button lets you decide.

```yaml
- id: alarm_auto_force_arm
  alias: 'Alarm: auto force-arm when leaving'
  triggers:
  - trigger: event
    event_type: verisure_italy_arming_exception
  conditions:
  - condition: template
    value_template: "{{ trigger.event.data.mode == 'armed_away' }}"
  actions:
  - action: notify.mobile_app_you
    data:
      message: "Open zones: {{ trigger.event.data.zones | join(', ') }} — force-arming..."
  - action: verisure_italy.force_arm
    data:
      entity_id: alarm_control_panel.verisure_alarm
  mode: single
```

## Night arm when both residents are in bedroom

Arms home when both residents are in the bedroom, between midnight
and 7AM. Triggers at midnight if already in bed, or when the second
person enters the bedroom.

Requires room-level presence sensors (e.g. WiFi AP-based tracking
via OpenWrt, ESPresense BLE, or mmWave sensors). Adapt the
`sensor.*_room` entities to match your setup.

```yaml
- id: alarm_night_arm
  alias: 'Alarm: night arm when both in bedroom'
  triggers:
  - trigger: time
    at: '00:00:00'
  - trigger: template
    value_template: >-
      {{ states('sensor.presence_person1_room') == 'bedroom'
         and states('sensor.presence_person2_room') == 'bedroom' }}
  conditions:
  - condition: time
    after: '00:00:00'
    before: '07:00:00'
  - condition: state
    entity_id: alarm_control_panel.verisure_alarm
    state: disarmed
  - condition: template
    value_template: >-
      {{ states('sensor.presence_person1_room') == 'bedroom'
         and states('sensor.presence_person2_room') == 'bedroom' }}
  actions:
  - action: alarm_control_panel.alarm_arm_home
    target:
      entity_id: alarm_control_panel.verisure_alarm
  - action: notify.mobile_app_you
    data:
      message: Both in bedroom — night alarm armed
  mode: single
```

## Morning disarm

Disarms the alarm at 7AM if it's armed.

```yaml
- id: alarm_morning_disarm
  alias: 'Alarm: morning disarm'
  triggers:
  - trigger: time
    at: '07:00:00'
  conditions:
  - condition: not
    conditions:
    - condition: state
      entity_id: alarm_control_panel.verisure_alarm
      state: disarmed
  actions:
  - action: alarm_control_panel.alarm_disarm
    target:
      entity_id: alarm_control_panel.verisure_alarm
  - action: notify.mobile_app_you
    data:
      message: Morning — alarm disarmed
  mode: single
```

## Alarm state change notification

Sends a push notification on every alarm state change.

```yaml
- id: alarm_notify_state_change
  alias: 'Alarm: notify state change'
  triggers:
  - trigger: state
    entity_id: alarm_control_panel.verisure_alarm
    to:
    - disarmed
    - armed_home
    - armed_away
  actions:
  - action: notify.mobile_app_you
    data:
      message: "Alarm: {{ trigger.to_state.state }}"
  mode: single
```

## Available event data

The `verisure_italy_arming_exception` event provides:

| Field | Type | Description |
|-------|------|-------------|
| `entity_id` | string | The alarm entity that triggered it |
| `zones` | list[string] | Names of open zones (e.g. `["Finstudio1", "Cucina"]`) |
| `mode` | string | Attempted arm mode (`armed_home` or `armed_away`) |

The Force Arm button entity (`button.verisure_force_arm`) exposes
`open_zones` and `mode` as state attributes when available.
