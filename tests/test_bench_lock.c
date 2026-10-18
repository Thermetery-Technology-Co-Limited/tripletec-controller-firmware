/* Exercise the real application command path with only the physical board
 * boundary stubbed. The renamed embedded entry point is discarded at link. */
static int board_application_main(void);
#define main board_application_main
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wreturn-type"
#include "../app/main.c"
#pragma GCC diagnostic pop
#undef main

#include <assert.h>

static unsigned force_off_calls;
static bool expected_lock_at_force_off;

/* The separate commissioning engine is exercised in test_commissioning.
 * Keep this application's lock-command test isolated from its ADC backend. */
uint32_t board_millis(void) { return 1000U; }
void commissioning_init(void) {}
void commissioning_stop(uint32_t reason, uint32_t now) { (void)reason; (void)now; }
bool commissioning_active(void) { return false; }
bool commissioning_owns_adc(void) { return false; }
void commissioning_snapshot(commission_telemetry_t *dest, uint32_t now)
{ memset(dest, 0, sizeof(*dest)); dest->uptime_ms = now; }
control_result_t commissioning_prepare(const tec_inputs_t *in, const tec_controller_t *ctrl,
                                       bool locked, int32_t value, uint32_t now)
{ (void)in; (void)ctrl; assert(locked); (void)value; (void)now; return CONTROL_INTERLOCK; }
control_result_t commissioning_start(const tec_inputs_t *in, const tec_controller_t *ctrl,
                                     bool locked, int32_t value, uint32_t now)
{ (void)in; (void)ctrl; (void)locked; (void)value; (void)now; return CONTROL_INTERLOCK; }

void board_outputs_force_off(void)
{
    ++force_off_calls;
    /* Every transition must disarm and clear control history before touching
     * the physical output, and must touch the physical output before unlock. */
    assert(bench_outputs_locked == expected_lock_at_force_off);
    for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(!controller.channel[i].armed);
        assert(controller.channel[i].duty == 0.0f);
        assert(controller.channel[i].integral == 0.0f);
    }
}

bool board_button_pressed(void)
{
    return false;
}

static void dirty_control_history(void)
{
    /* Seed an armed state directly, even though commissioning currently
     * forbids entering one. This proves transitions clear future live state. */
    for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        controller.channel[i].armed = true;
        controller.channel[i].duty = 0.2f + (float)i * 0.1f;
        controller.channel[i].integral = 0.5f + (float)i;
    }
}

static void assert_policy_unchanged(const tec_config_t *before)
{
    assert(memcmp(&controller.config, before, sizeof(*before)) == 0);
    assert(controller.config.current_limit_a[0] == 0.0f);
    assert(controller.config.current_limit_a[1] == 9.0f);
    assert(controller.config.current_limit_a[2] == 9.0f);
    assert(!inputs.hardware_verified);
    for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(!controller.config.current_limit_commissioned[i]);
        assert(!inputs.current[i].calibrated);
    }
}

static void assert_published_state(bool locked, uint32_t now)
{
    tec_controller_step(&controller, &inputs, now, &outputs);
    publish(now);
    assert(g_telemetry.outputs_locked == (locked ? 1U : 0U));
    assert((g_telemetry.valid_flags & (1U << 6)) != 0U);
    assert(g_telemetry.armed_mask == 0U);
    assert((g_telemetry.seq & 1U) == 0U);
    for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        assert(g_telemetry.applied_duty_milli[i] == 0U);
        assert(g_telemetry.desired_duty_milli[i] == 0U);
        assert((g_telemetry.active_faults[i] & TEC_FAULT_HARDWARE_GATE) != 0U);
        assert((g_telemetry.active_faults[i] & TEC_FAULT_CURRENT_UNVERIFIED) != 0U);
        assert((g_telemetry.active_faults[i] & TEC_FAULT_CURRENT_UNCOMMISSIONED) != 0U);
    }
    assert(g_limit_configuration.current_limit_ma[0] == 0U);
    assert(g_limit_configuration.current_limit_ma[1] == 9000U);
    assert(g_limit_configuration.current_limit_ma[2] == 9000U);
    assert(g_limit_configuration.commissioned_mask == 0U);
}

static void set_lock_and_assert(int32_t value, uint32_t now)
{
    const unsigned calls_before = force_off_calls;
    expected_lock_at_force_off = bench_outputs_locked;
    dirty_control_history();
    assert(apply_command(NULL, CONTROL_SET_BENCH_LOCK, value, now) == CONTROL_OK);
    assert(force_off_calls == calls_before + 1U);
    assert(bench_outputs_locked == (value != 0));
    assert_published_state(value != 0, now);
}

int main(void)
{
    (void)&board_application_main;
    tec_config_t config;
    tec_config_defaults(&config);
    assert(tec_controller_init(&controller, &config));
    inputs.ambient_c = 28.0f;
    inputs.rh_percent = 50.0f;
    inputs.ambient_valid = true;
    inputs.ambient_timestamp_ms = 1000U;
    for (size_t i = 0U; i < TEC_CHANNEL_COUNT; ++i) {
        inputs.plate[i] = (tec_plate_sample_t){25.0f, true, 1000U};
        inputs.current[i] = (tec_current_sample_t){0.0f, true, false, 1000U};
    }

    assert(bench_outputs_locked); /* Reset default must never inherit unlock. */
    assert_published_state(true, 1000U);
    assert_policy_unchanged(&config);

    expected_lock_at_force_off = true;
    dirty_control_history();
    assert(apply_command(NULL, CONTROL_ARM, 7, 1001U) == CONTROL_BENCH_LOCKED);
    assert(force_off_calls == 1U);
    assert_published_state(true, 1001U);

    set_lock_and_assert(0, 1002U);
    assert_policy_unchanged(&config);
    expected_lock_at_force_off = false;
    dirty_control_history();
    assert(apply_command(NULL, CONTROL_ARM, 7, 1003U) == CONTROL_INTERLOCK);
    assert(force_off_calls == 3U);
    assert_published_state(false, 1003U);
    assert_policy_unchanged(&config);

    /* Repeated requests are also stop operations; they cannot preserve an
     * armed channel or carry its PI history through an unlock request. */
    set_lock_and_assert(0, 1004U);
    set_lock_and_assert(1, 1005U);
    set_lock_and_assert(1, 1006U);
    assert_policy_unchanged(&config);

    const int32_t invalid_values[] = {-1, 2, INT32_MIN, INT32_MAX};
    for (size_t state = 0U; state < 2U; ++state) {
        set_lock_and_assert((int32_t)state, 1010U + (uint32_t)state);
        for (size_t i = 0U; i < sizeof(invalid_values) / sizeof(invalid_values[0]); ++i) {
            const unsigned calls_before = force_off_calls;
            assert(apply_command(NULL, CONTROL_SET_BENCH_LOCK, invalid_values[i],
                                 1020U) == CONTROL_BAD_VALUE);
            assert(force_off_calls == calls_before);
            assert(bench_outputs_locked == (state != 0U));
            assert_published_state(state != 0U, 1020U);
            assert_policy_unchanged(&config);
        }
    }
    set_lock_and_assert(0, 1030U);
    expected_lock_at_force_off = false;
    assert(apply_command(NULL, CONTROL_COMMISSION_PREPARE, 15000, 1031U) == CONTROL_INTERLOCK);
    assert(bench_outputs_locked); /* Even a rejected preparation restores OFF. */
    assert_policy_unchanged(&config);
    puts("runtime bench lock: stop-before-transition, telemetry, invalid-value and independent interlock tests passed");
    return 0;
}
