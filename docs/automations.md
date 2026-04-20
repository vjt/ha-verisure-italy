# Example Automations

Ready-to-use automations for the Verisure Italy integration. Copy, adapt
entity names to your setup, and paste into your `automations.yaml`.

> **Placeholder entities** — replace `notify.mobile_app_you` with your
> actual notify service. Replace `person.*` and `sensor.*_room` entities
> with yours.

---

## Prerequisites

### Presence sensors

Several automations need to know whether anyone is home. Create these
template binary sensors in your `configuration.yaml` (or a file included
from it via `template: !include template.yaml`):

```yaml
template:
- binary_sensor:
  - unique_id: residents_home
    name: Residents Home
    device_class: presence
    state: >
      {{ is_state('person.person1', 'home')
         or is_state('person.person2', 'home') }}

  - unique_id: anyone_home
    name: Anyone Home
    device_class: presence
    state: >
      {{ is_state('binary_sensor.residents_home', 'on') }}
```

**`residents_home`** is `on` when any resident is home. **`anyone_home`**
wraps it so you can extend it with non-residents (e.g. a cleaning person
tracked via a separate device tracker) without touching the alarm
automations.

> **Tip:** if you have WiFi-based presence detection (e.g.
> [OpenWrt device trackers](https://github.com/vjt/openwrt-ha-presence)),
> add those as extra `or` conditions for faster
> detection than GPS-based `person` entities:
> ```yaml
>     state: >
>       {{ is_state('person.person1', 'home')
>          or is_state('person.person2', 'home')
>          or is_state('device_tracker.person1_wifi', 'home')
>          or is_state('device_tracker.person2_wifi', 'home') }}
> ```

---

## Arm & Disarm

### :rotating_light: Auto arm when leaving home

Arms away when everyone has left. Requires a `binary_sensor` that
tracks whether anyone is home (e.g. via the `group` integration,
Bluetooth, or router-based presence detection).

**Post-check:** after the service call, waits up to 60s for the state
to change to `armed_away`. If it doesn't, the service call was
dropped (e.g. entity `unavailable` during an API outage) and a
critical notification fires so a human can act. Without this, a
failed arm is **silent** — HA logs a WARNING and moves on.

```yaml
- id: alarm_arm_on_leave
  alias: "Alarm: arm away when the last one leaves"
  triggers:
  - trigger: state
    entity_id: binary_sensor.anyone_home
    to: "off"
  actions:
  - action: alarm_control_panel.alarm_arm_away
    target:
      entity_id: alarm_control_panel.verisure_alarm
  - wait_for_trigger:
    - trigger: state
      entity_id: alarm_control_panel.verisure_alarm
      to: armed_away
    timeout: "00:01:00"
    continue_on_timeout: true
  - choose:
    - conditions:
      - condition: template
        value_template: "{{ not wait.trigger }}"
      sequence:
      - action: notify.mobile_app_you
        data:
          message: >-
            CRITICAL: arm-away failed — alarm is
            "{{ states('alarm_control_panel.verisure_alarm') }}".
            Nobody home. Check Verisure app NOW.
          data:
            push:
              interruption-level: critical
              sound:
                name: default
                critical: 1
                volume: 1.0
    default:
    - action: notify.mobile_app_you
      data:
        message: Nobody home — alarm armed away
  mode: single
```

### :shield: Safety net — arm when away and disarmed

Catches edge cases where the primary arm-on-leave automation misses
(e.g. HA restart, presence sensor glitch). Two triggers: reactive
(alarm goes disarmed while nobody's home) and periodic (every 5 min).

**Note:** the condition `state: disarmed` fails silently when the
entity is `unavailable` — this is intentional. The separate
[**Alert on prolonged unavailable**](#ambulance-alert-on-prolonged-unavailable)
automation covers that failure mode. The post-check below catches
the case where the service call is issued but doesn't land.

```yaml
- id: alarm_safety_net
  alias: "Alarm: safety net — arm when away and disarmed"
  description: >-
    If nobody is home and the alarm is disarmed, arm it and notify.
    Catches missed arm-on-leave triggers.
  triggers:
  - trigger: state
    entity_id: alarm_control_panel.verisure_alarm
    to: disarmed
    id: reactive
  - trigger: time_pattern
    minutes: /5
    id: periodic
  conditions:
  - condition: state
    entity_id: binary_sensor.anyone_home
    state: "off"
  - condition: state
    entity_id: alarm_control_panel.verisure_alarm
    state: disarmed
  actions:
  - action: alarm_control_panel.alarm_arm_away
    target:
      entity_id: alarm_control_panel.verisure_alarm
  - wait_for_trigger:
    - trigger: state
      entity_id: alarm_control_panel.verisure_alarm
      to: armed_away
    timeout: "00:01:00"
    continue_on_timeout: true
  - choose:
    - conditions:
      - condition: template
        value_template: "{{ not wait.trigger }}"
      sequence:
      - action: notify.mobile_app_you
        data:
          message: >-
            CRITICAL: safety net failed to arm — alarm is
            "{{ states('alarm_control_panel.verisure_alarm') }}".
          data:
            push:
              interruption-level: critical
              sound:
                name: default
                critical: 1
                volume: 1.0
    default:
    - action: notify.mobile_app_you
      data:
        message: "Safety net: nobody home and alarm was disarmed — arming now"
  mode: single
```

### :crescent_moon: Night arm when both residents are in bedroom

Arms home between midnight and 7AM when both residents are in the
bedroom. Triggers at midnight if already in bed, or when the second
person enters.

Requires room-level presence sensors (e.g. [WiFi AP-based tracking
via OpenWrt](https://github.com/vjt/openwrt-ha-presence), ESPresense
BLE, or mmWave sensors).

```yaml
- id: alarm_night_arm
  alias: "Alarm: night arm when both in bedroom"
  triggers:
  - trigger: time
    at: "00:00:00"
  - trigger: template
    value_template: >-
      {{ states('sensor.presence_person1_room') == 'bedroom'
         and states('sensor.presence_person2_room') == 'bedroom' }}
  conditions:
  - condition: time
    after: "00:00:00"
    before: "07:00:00"
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

### :sunny: Morning disarm

Disarms the alarm at 7AM if it's armed.

```yaml
- id: alarm_morning_disarm
  alias: "Alarm: morning disarm"
  triggers:
  - trigger: time
    at: "07:00:00"
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

---

## Force Arm

### :muscle: Auto force-arm when leaving

When arming away fails due to open zones, notify about which windows
are open and force-arm automatically. Only triggers for `armed_away`
(leaving home) — for `armed_home` (night mode) the dashboard Force
Arm button lets you decide.

```yaml
- id: alarm_auto_force_arm
  alias: "Alarm: auto force-arm when leaving"
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

---

## Notifications

### :loudspeaker: Alarm state change

Push notification on every alarm state change.

```yaml
- id: alarm_notify_state_change
  alias: "Alarm: notify state change"
  triggers:
  - trigger: state
    entity_id: alarm_control_panel.verisure_alarm
    to:
    - disarmed
    - armed_home
    - armed_away
    - armed_custom_bypass
  actions:
  - action: notify.mobile_app_you
    data:
      message: "Alarm: {{ trigger.to_state.state }}"
  mode: single
```

### :bell: Actionable disarm notification on arrival

When someone arrives home and the alarm is armed, sends a push
notification with a **Disarm** action button. Auto-dismissed if the
alarm gets disarmed by other means. Runs in parallel mode so both
residents get their own notification.

```yaml
- id: alarm_notify_disarm_on_arrival
  alias: "Alarm: notify to disarm on arrival"
  triggers:
  - trigger: zone
    entity_id:
    - person.person1
    - person.person2
    zone: zone.home
    event: enter
  conditions:
  - condition: not
    conditions:
    - condition: state
      entity_id: alarm_control_panel.verisure_alarm
      state: disarmed
  actions:
  - variables:
      notify_target: >-
        {% if trigger.entity_id == 'person.person1' %}
          notify.mobile_app_person1
        {% else %}
          notify.mobile_app_person2
        {% endif %}
  - action: "{{ notify_target }}"
    data:
      message: Alarm is armed! Disarm?
      data:
        tag: alarm_disarm_prompt
        actions:
        - action: DISARM_ALARM
          title: Disarm
  - wait_for_trigger:
    - trigger: event
      event_type: mobile_app_notification_action
      event_data:
        action: DISARM_ALARM
    - trigger: state
      entity_id: alarm_control_panel.verisure_alarm
      to: disarmed
    timeout: "00:05:00"
    continue_on_timeout: true
  - choose:
    - conditions:
      - condition: template
        value_template: "{{ wait.trigger and wait.trigger.platform == 'event' }}"
      sequence:
      - action: alarm_control_panel.alarm_disarm
        target:
          entity_id: alarm_control_panel.verisure_alarm
  mode: parallel
  max: 2
```

### :ambulance: Alert on prolonged unavailable

**Security-critical.** The alarm entity has been `unavailable` for 5
minutes. Transient API glitches are absorbed by the client's retry
layer (HTTP 5xx / timeouts, exponential backoff up to ~15s), so this
does NOT fire for every hiccup. A sustained outage means:

- Service calls to the entity are **silently dropped** by HA
- Arm/disarm automations stop working
- Force-arm, force-disarm all dead

A human needs to know. The 5-minute `for:` duration is the sweet spot
— short enough to catch real problems, long enough to ignore noise.

```yaml
- id: alarm_unavailable_alert
  alias: "Alarm: alert on prolonged unavailable"
  triggers:
  - trigger: state
    entity_id: alarm_control_panel.verisure_alarm
    to: unavailable
    for: "00:05:00"
  actions:
  - action: notify.mobile_app_you
    data:
      message: >-
        ALARM: entity unavailable for 5min. Arm/disarm automations
        are DEAD until this recovers. Check HA logs + Verisure app.
      data:
        push:
          interruption-level: critical
          sound:
            name: default
            critical: 1
            volume: 1.0
  mode: single
```

### :warning: Unknown alarm state alert

**Security-critical.** The alarm reported a state code the integration
doesn't recognize. The alarm entity goes unavailable. Sends a critical
push notification (bypasses Do Not Disturb on iOS).

```yaml
- id: alarm_unknown_state_alert
  alias: "Alarm: alert on unknown state"
  triggers:
  - trigger: event
    event_type: verisure_italy_unknown_state
  actions:
  - action: notify.mobile_app_you
    data:
      message: >-
        ALARM: unknown state code "{{ trigger.event.data.proto_code }}"
        — entity unavailable. Check the Verisure app NOW.
      data:
        push:
          sound:
            name: default
            critical: 1
            volume: 1.0
  mode: single
```

---

## Events Reference

### `verisure_italy_arming_exception`

Fired when arming is blocked by open zones.

| Field | Type | Description |
|-------|------|-------------|
| `entity_id` | string | The alarm entity |
| `zones` | list | Names of open zones (e.g. `["Window Studio", "Kitchen"]`) |
| `mode` | string | Attempted arm mode: `armed_home` or `armed_away` |

### `verisure_italy_unknown_state`

Fired when the panel reports an unrecognized proto code.

| Field | Type | Description |
|-------|------|-------------|
| `proto_code` | string | The unknown code from the panel |
| `installation` | string | Installation number |
