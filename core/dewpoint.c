#include "dewpoint.h"

#include <math.h>
#include <stddef.h>

/* Sensirion, Introduction to Humidity, Table 1 and equation 12:
 * https://sensirion.com/en/media/documents/8AB2AD38/61642ADD/Sensirion_AppNotes_Humidity_Sensors_Introduction_to_Relative_Humidit.pdf
 * Water coefficients: a=17.62, b=243.12 C, valid from -45 to +60 C.
 * Below freezing, liquid-water dew point differs from frost point. */
bool tec_dewpoint_c(float ambient_c, float rh_percent, float *dewpoint_c)
{
    const float a = 17.62f;
    const float b = 243.12f;
    float gamma;
    float result;

    if (dewpoint_c == NULL) {
        return false;
    }
    *dewpoint_c = NAN;
    if (!isfinite(ambient_c) || !isfinite(rh_percent) ||
        ambient_c < TEC_DEWPOINT_MIN_C || ambient_c > TEC_DEWPOINT_MAX_C ||
        rh_percent <= 0.0f || rh_percent > 100.0f) {
        return false;
    }
    /* At saturation the exact identity also avoids endpoint roundoff. */
    if (rh_percent == 100.0f) {
        *dewpoint_c = ambient_c;
        return true;
    }
    gamma = logf(rh_percent / 100.0f) + a * ambient_c / (b + ambient_c);
    result = b * gamma / (a - gamma);
    if (!isfinite(result) || result < TEC_DEWPOINT_MIN_C ||
        result > TEC_DEWPOINT_MAX_C) {
        return false;
    }
    *dewpoint_c = result;
    return true;
}
