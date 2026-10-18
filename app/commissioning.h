#ifndef TEC_COMMISSIONING_H
#define TEC_COMMISSIONING_H
#include "control_protocol.h"
#include "controller.h"
#include "telemetry.h"

void commissioning_init(void);
control_result_t commissioning_prepare(const tec_inputs_t *in, const tec_controller_t *ctrl,
                                       bool bench_locked, int32_t trip_ma, uint32_t now);
control_result_t commissioning_start(const tec_inputs_t *in, const tec_controller_t *ctrl,
                                     bool bench_locked, int32_t kind, uint32_t now);
/* Kinds 5/6 supervise a fixed 27 C target for at most 5 minutes, respectively
 * with TEC1 drive or GPIO-LOW dry operation. The independent dew-point floor
 * can inhibit/stop this target; it never changes the requested 27 C target.
 */
void commissioning_stop(uint32_t reason, uint32_t now);
void commissioning_tick(const tec_inputs_t *in, const tec_controller_t *ctrl,
                        bool bench_locked, uint32_t now, uint32_t last_host_ms);
bool commissioning_active(void);
bool commissioning_owns_adc(void);
void commissioning_snapshot(commission_telemetry_t *dest, uint32_t now);
#endif
