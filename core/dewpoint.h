#ifndef TEC_DEWPOINT_H
#define TEC_DEWPOINT_H

#include <stdbool.h>

/* Magnus approximation over liquid water. Both temperatures use degrees C.
 * RH is percent, not a 0..1 fraction. This function rejects extrapolation. */
#define TEC_DEWPOINT_MIN_C (-45.0f)
#define TEC_DEWPOINT_MAX_C (60.0f)

bool tec_dewpoint_c(float ambient_c, float rh_percent, float *dewpoint_c);

#endif
