"""
charge_headroom.py - lumped estimate of how much more charge current a
battery bank could take before its highest cell reaches v_limit.

For allocation only: the hard limit is still enforced by the inverter's
own charge-voltage regulation. Errors here cost allocation efficiency,
not cell safety.

Model, over a fixed window of `window` seconds (N samples):
    v[n] - v[n-N] = R * (i[n] - i[n-N]) + c
R : effective resistance on the window timescale (cell V per pack A). It
    lumps ohmic, polarisation and OCV-slope effects together, so it
    rises by itself as the cell approaches full.
c : voltage drift over the window at the present current (relaxation
    plus filling). It is positive while charging toward the knee.

Headroom:
    i_avail = i + (v_limit - v - c) / R

R and c are fitted by 2-parameter recursive least squares (RLS) with a
forgetting factor. The full fit only runs when the current has actually
changed over the window. Pure Python; call update() at a steady rate.
"""

from collections import deque

# Specific resistance R_seen * Q_bank [Ohm*Ah] is roughly constant for a cell
# family
K_TYP = 0.090
K_MIN = 0.015

# Used when the battery capacity is unknown
DEFAULT_CAPACITY = 100.0

class ChargeHeadroom:
    def __init__(self, v_limit=3.48, window=20.0, sample_time=1.0,
                 i_max=300.0, forgetting=0.99,
                 output_time_constant=10.0, drift_gain=0.02,
                 capacity=None):
        """
        v_limit              : max cell voltage [V]
        window               : comparison window [s]
        sample_time          : interval between update() calls [s]
        i_max                : upper clamp on the reported headroom [A]
        forgetting           : RLS forgetting factor (0..1, closer to 1 =
                               longer memory)
        output_time_constant : low-pass filter on the reported headroom [s]
        drift_gain           : gain for tracking c when the current is steady
        capacity             : callable returning the battery capacity [Ah],
                               or None when unknown
        """
        self.v_limit = v_limit
        self.window_samples = max(1, int(round(window / sample_time)))
        self.sample_time = sample_time
        self.i_max = i_max
        self.forgetting = forgetting
        self.output_time_constant = output_time_constant
        self.drift_gain = drift_gain
        self._capacity = capacity

        # (voltage, current) samples spanning one window
        self.history = deque(maxlen=self.window_samples + 1)

        # Fitted parameters, kept in mOhm and mV for good numerical
        # conditioning; the resistance and drift properties convert to SI.
        self.resistance_mohm = 1e3 * K_TYP / self.capacity
        self.drift_mv = 0.0

        # RLS covariance of (resistance_mohm, drift_mv): a measure of how
        # uncertain each estimate is. Large values mean the next sample
        # moves the estimate a lot.
        self.covariance = [[10.0, 0.0], [0.0, 10.0]]
        self.max_variance = 100.0

        self.i_avail = 0.0

    @property
    def capacity(self):
        """Battery capacity [Ah], falling back to a default when unknown."""
        c = self._capacity() if self._capacity is not None else None
        return c if c else DEFAULT_CAPACITY

    @property
    def resistance(self):
        """Estimated resistance R [ohm], never below K_MIN / capacity."""
        return max(self.resistance_mohm * 1e-3, K_MIN / self.capacity)

    @property
    def drift(self):
        """Estimated voltage drift c over the window [V], never negative."""
        return max(self.drift_mv * 1e-3, 0.0)

    @property
    def min_current_step(self):
        return min(max(0.005 / self.resistance, self.capacity * 0.01), self.capacity * 0.1)

    def update(self, v_cell_max, i_pack):
        """v_cell_max [V], i_pack [A, + = charging].
        Returns the filtered available charge current [A]."""
        self.history.append((v_cell_max, i_pack))
        if len(self.history) > self.window_samples:
            v_old, i_old = self.history[0]
            delta_v_mv = (v_cell_max - v_old) * 1e3
            delta_i = i_pack - i_old
            if abs(delta_i) >= self.min_current_step:
                self._fit(delta_i, delta_v_mv)
            else:
                # Current hasn't changed enough, so R can't be identified,
                # but drift c still can: nudge it toward what's left of
                # delta_v after the current-step part is removed.
                residual = (delta_v_mv - self.resistance_mohm * delta_i
                            - self.drift_mv)
                self.drift_mv += self.drift_gain * residual

        headroom = (i_pack
                    + (self.v_limit - v_cell_max - self.drift)
                    / self.resistance)
        headroom = min(max(headroom, 0.0), self.i_max)

        # First-order low-pass on the output
        alpha = min(1.0, self.sample_time / self.output_time_constant)
        self.i_avail += alpha * (headroom - self.i_avail)
        return self.i_avail

    def _fit(self, delta_i, delta_v_mv):
        """One RLS step for the model delta_v = R * delta_i + c * 1."""
        P = self.covariance
        lam = self.forgetting

        # P * x, where x = (delta_i, 1) is the regressor vector
        P_x = [P[0][0] * delta_i + P[0][1],
               P[1][0] * delta_i + P[1][1]]

        # Gain: how much of the prediction error goes into each parameter
        denominator = lam + delta_i * P_x[0] + P_x[1]
        gain = [P_x[0] / denominator, P_x[1] / denominator]

        predicted_dv = self.resistance_mohm * delta_i + self.drift_mv
        error = delta_v_mv - predicted_dv
        self.resistance_mohm += gain[0] * error
        self.drift_mv += gain[1] * error

        # Shrink the covariance by what this sample taught us, then inflate
        # it by the forgetting factor so old data gradually loses weight.
        for row in range(2):
            for col in range(2):
                P[row][col] = (P[row][col] - gain[row] * P_x[col]) / lam

        # Anti wind-up: without excitation, the forgetting factor would make
        # the variances grow without bound.
        for k in range(2):
            P[k][k] = min(P[k][k], self.max_variance)
