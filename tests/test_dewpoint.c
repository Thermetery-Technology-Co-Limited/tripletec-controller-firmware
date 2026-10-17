#include "dewpoint.h"

#include <assert.h>
#include <math.h>
#include <stddef.h>
#include <stdio.h>

static void reference_examples(void)
{
    float d;
    assert(tec_dewpoint_c(25.0f, 50.0f, &d));
    assert(fabsf(d - 13.8515836f) < 0.001f);
    assert(tec_dewpoint_c(30.0f, 80.0f, &d));
    assert(fabsf(d - 26.1688459f) < 0.001f);
    assert(tec_dewpoint_c(0.0f, 50.0f, &d));
    assert(fabsf(d - (-9.2020198f)) < 0.001f);
    assert(tec_dewpoint_c(-10.0f, 80.0f, &d));
    assert(fabsf(d - (-12.7968863f)) < 0.001f);
}

static void physical_properties(void)
{
    float t;
    float rh;
    float previous;
    float d;
    for (t = -40.0f; t <= 60.0f; t += 5.0f) {
        assert(tec_dewpoint_c(t, 100.0f, &d));
        assert(d == t);
        previous = -INFINITY;
        for (rh = 10.0f; rh <= 100.0f; rh += 5.0f) {
            if (tec_dewpoint_c(t, rh, &d)) {
                assert(d <= t + 0.0001f);
                assert(d >= previous);
                previous = d;
            }
        }
    }
    previous = -INFINITY;
    for (t = 0.0f; t <= 60.0f; t += 1.0f) {
        assert(tec_dewpoint_c(t, 50.0f, &d));
        assert(d > previous);
        previous = d;
    }
}

static void invalid_inputs(void)
{
    float d = 123.0f;
    assert(!tec_dewpoint_c(25.0f, 50.0f, NULL));
    assert(!tec_dewpoint_c(25.0f, 0.0f, &d) && isnan(d));
    assert(!tec_dewpoint_c(25.0f, -1.0f, &d));
    assert(!tec_dewpoint_c(25.0f, 100.01f, &d));
    assert(!tec_dewpoint_c(NAN, 50.0f, &d));
    assert(!tec_dewpoint_c(INFINITY, 50.0f, &d));
    assert(!tec_dewpoint_c(25.0f, NAN, &d));
    assert(!tec_dewpoint_c(25.0f, INFINITY, &d));
    assert(!tec_dewpoint_c(-45.01f, 100.0f, &d));
    assert(!tec_dewpoint_c(60.01f, 100.0f, &d));
    assert(tec_dewpoint_c(-45.0f, 100.0f, &d) && d == -45.0f);
    assert(tec_dewpoint_c(60.0f, 100.0f, &d) && d == 60.0f);
    /* Ambient is valid but the resulting dew point is outside the fit. */
    assert(!tec_dewpoint_c(-40.0f, 10.0f, &d));
}

int main(void)
{
    reference_examples();
    physical_properties();
    invalid_inputs();
    puts("dewpoint tests passed");
    return 0;
}
