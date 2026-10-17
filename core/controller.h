#ifndef TEC_CONTROLLER_H
#define TEC_CONTROLLER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define TEC_CHANNEL_COUNT 3U

typedef enum {
    TEC_MODE_REGULATED = 0,
    TEC_MODE_UNREGULATED = 1
} tec_mode_t;

typedef enum {
    TEC_FAULT_NONE = 0U,
    TEC_FAULT_CONFIGURATION = 1U << 0,
    TEC_FAULT_HARDWARE_GATE = 1U << 1,
    TEC_FAULT_AMBIENT_INVALID = 1U << 2,
    TEC_FAULT_AMBIENT_STALE = 1U << 3,
    TEC_FAULT_PLATE_INVALID = 1U << 4,
    TEC_FAULT_PLATE_STALE = 1U << 5,
    TEC_FAULT_TIMING = 1U << 6,
    TEC_FAULT_TARGET_RANGE = 1U << 7,
    TEC_FAULT_ARITHMETIC = 1U << 8,
    TEC_FAULT_CURRENT_INVALID = 1U << 9,
    TEC_FAULT_CURRENT_STALE = 1U << 10,
    TEC_FAULT_CURRENT_UNVERIFIED = 1U << 11,
    TEC_FAULT_CURRENT_UNCOMMISSIONED = 1U << 12,
    TEC_FAULT_CURRENT_OVERLIMIT = 1U << 13
} tec_fault_t;

typedef struct {
    float dewpoint_margin_c;
    /* Regulated mode requires >=0 C; unregulated mode deliberately bypasses
     * condensation/frost protection and this setpoint floor. */
    float absolute_min_c;
    float ambient_min_c;
    float ambient_max_c;
    float plate_min_c;         /* hard stop at or below this operating bound */
    float plate_max_c;         /* hard stop at or above this operating bound */
    float restart_hysteresis_c; /* extra warmth before resuming after guard stop */
    uint32_t sample_timeout_ms;
    uint32_t max_step_ms;
    float kp;                  /* duty / degree C */
    float ki;                  /* duty / (degree C * second) */
    float max_duty;            /* 0 < maximum <= 1 */
    float rise_per_second;     /* duty / second; reductions are immediate */
    /* Zero means uncommissioned: arming is refused. Per-channel limits must
     * be verified against wiring, TEC and driver ratings before enabling.
     * Maximum representable sensor ranges are 50 A,10 A,10 A respectively. */
    float current_limit_a[TEC_CHANNEL_COUNT];
} tec_config_t;

typedef struct {
    float temperature_c;
    bool valid;
    uint32_t timestamp_ms;
} tec_plate_sample_t;

typedef struct {
    float amperes;
    bool valid;
    bool calibrated;
    uint32_t timestamp_ms;
} tec_current_sample_t;

typedef struct {
    float ambient_c;
    float rh_percent;
    bool ambient_valid;        /* false on any acquisition/CRC/range error */
    uint32_t ambient_timestamp_ms;
    tec_plate_sample_t plate[TEC_CHANNEL_COUNT];
    tec_current_sample_t current[TEC_CHANNEL_COUNT];
    /* Must come from board commissioning, never from simulated sensor data. */
    bool hardware_verified;
} tec_inputs_t;

typedef struct {
    bool armed;
    float duty;
    float integral;
    uint32_t latched_faults;
    bool guard_wait;
} tec_channel_state_t;

typedef struct {
    tec_config_t config;
    tec_mode_t mode;
    tec_channel_state_t channel[TEC_CHANNEL_COUNT];
    uint32_t last_step_ms;
    bool have_last_step;
    bool configuration_valid;
} tec_controller_t;

typedef struct {
    float duty;                /* desired cooling only; never a heating request */
    bool armed;
    bool guard_inhibited;      /* normal zero demand at/below the target floor */
    uint32_t active_faults;
    uint32_t latched_faults;
} tec_channel_output_t;

typedef struct {
    float dewpoint_c;          /* NAN if ambient calculation is unavailable */
    float target_c;            /* regulated target; NAN in unregulated mode */
    tec_channel_output_t channel[TEC_CHANNEL_COUNT];
} tec_outputs_t;

void tec_config_defaults(tec_config_t *config);
bool tec_controller_init(tec_controller_t *controller, const tec_config_t *config);

/* Actual mode changes immediately zero every stored duty/integral and disarm
 * every channel. A repeated selection of the current mode preserves state.
 * Unregulated requests 100% steady cooling subject to slew and interlocks;
 * max_duty applies only to regulated PI control. */
bool tec_controller_set_mode(tec_controller_t *controller, tec_mode_t mode);

/* Arming is explicit and per channel. Rearming clears its latched faults and
 * resets its integral/output. The next step still verifies every interlock.
 * A fault while armed disarms that channel; sensor recovery never rearms it.
 * Arming is refused when that channel's configured current limit is zero. */
bool tec_controller_set_armed(tec_controller_t *controller, size_t channel, bool armed);

/* Call regularly with a monotonic uint32 millisecond clock. Unsigned elapsed
 * time supports its ordinary wraparound. At first call, output stays zero.
 * Mutating controller.config after init is unsupported: reinitialize instead.
 * The caller must apply returned duty through the board's independent inhibit. */
void tec_controller_step(tec_controller_t *controller, const tec_inputs_t *inputs,
                         uint32_t now_ms, tec_outputs_t *outputs);

#endif
