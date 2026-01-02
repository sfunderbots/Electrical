import numpy as np
import matplotlib.pyplot as plt
from scipy.special import ellipk, ellipe

# ============================
# Physical constants
# ============================
mu0 = 4 * np.pi * 1e-7
M = 1e6  # magnetization (A/m)

# ============================
# Magnet geometry
# ============================
R = 3e-3
L = 3e-3
z0 = 2.5e-3

# ============================
# Reed geometry (sensor model ONLY)
# ============================
reed_length = 6.5e-3
half_reed = reed_length / 2

# ============================
# Spatial domain
# ============================
x_vals = np.linspace(-20e-3, 20e-3, 10001)
x_pos = x_vals[x_vals >= 0]  # only positive side for plotting

# ============================
# Elliptic geometry helper
# ============================
def ellip_geom(r, zp):
    return (R + r) / np.hypot(R + r, zp)

# ============================
# Elliptic kernel
# ============================
def f_component(r, zp):
    denom = (R + r)**2 + zp**2
    if r == 0:
        m = 0.0
    else:
        m = 4 * R * r / denom
    m = min(m, 0.999999999)
    K = ellipk(m)
    E = ellipe(m)
    near = ellip_geom(-r, zp)
    far = ellip_geom(r, zp)
    return (near - far) * E + zp * K

# ============================
# Pole distances
# ============================
ztp = z0 - L / 2
zbp = z0 + L / 2

# ============================
# TRUE point field for x >= 0
# ============================
B_top_pos = mu0 * M / np.pi * np.array([f_component(x, ztp) for x in x_vals[x_vals >= 0]])
B_bottom_pos = mu0 * M / np.pi * np.array([f_component(x, zbp) for x in x_vals[x_vals >= 0]])
B_total_pos = B_top_pos - B_bottom_pos

# ============================
# Reflect for x < 0 using inverted symmetry
# ============================
B_total = np.zeros_like(x_vals)
B_total[x_vals >= 0] = B_total_pos
B_total[x_vals < 0] = -B_total_pos[1:][::-1]

# Normalize for plotting
B_norm = B_total / np.max(np.abs(B_total))

# ============================
# SENSOR MODEL (reed averaging — signed)
# ============================
B_reed_avg = np.zeros_like(B_norm)
for i, xc in enumerate(x_vals):
    window = (x_vals >= xc - half_reed) & (x_vals <= xc + half_reed)
    B_reed_avg[i] = np.mean(B_norm[window])  # signed average over reed length

# ============================
# Threshold evaluation
# ============================
x_ref = 3e-3  # approximate activation point of the reed
threshold = np.interp(x_ref, x_vals, B_reed_avg)
print(f"Reed threshold (signed avg) = {threshold:.3f}")

# ============================
# Find threshold crossings (positive X only)
# ============================
crossings = []
for i in range(len(x_vals) - 1):
    if x_vals[i] < 0:
        continue  # only positive X
    y1 = B_reed_avg[i] - threshold
    y2 = B_reed_avg[i+1] - threshold
    if y1 * y2 < 0:
        # linear interpolation
        x_cross = x_vals[i] + (0 - y1) * (x_vals[i+1] - x_vals[i]) / (y2 - y1)
        y_cross = np.interp(x_cross, x_vals, B_reed_avg)
        crossings.append((x_cross, y_cross))

# ============================
# Plot — positive X only
# ============================
plt.figure(figsize=(10, 6))
plt.plot(x_pos*1e3, B_norm[x_vals >= 0], label="Point field Bz(x)", linewidth=1.5)
plt.plot(x_pos*1e3, B_reed_avg[x_vals >= 0], label="Reed-averaged field (signed)", linewidth=2.5, linestyle='-.')

# Mark threshold line
plt.axhline(threshold, color=(0.8, 0, 0), linestyle='--', alpha=0.8, label="Reed threshold")

# Compute slope of reed-averaged field
B_slope = np.gradient(B_reed_avg, x_vals)

# Mark threshold crossings with slope-based vertical placement
for x_cross, y_cross in crossings:
    # find index nearest to crossing
    idx = np.searchsorted(x_vals, x_cross)
    slope = B_slope[idx]

    # decide vertical offset based on slope
    offset = 0.05  # adjust for plot scale
    vertical_shift = -offset if slope > 0 else offset  # slope > 0 -> label below, slope < 0 -> label above

    plt.scatter(x_cross*1e3, y_cross, color='red', zorder=6)
    plt.text(
        x_cross*1e3 + 0.2,        # keep horizontal shift like before
        y_cross + vertical_shift, # only move vertically
        f"x={x_cross*1e3:.2f} mm",
        fontsize=8,
        verticalalignment='center'
    )

# Find the peak of the reed-averaged field (B_reed_avg)
peak_idx = np.argmax(abs(B_reed_avg[x_vals >= 0]))
x_peak = x_vals[x_vals >= 0][peak_idx]
y_peak = B_reed_avg[x_vals >= 0][peak_idx]  # <-- use the average, not B_norm

plt.scatter(x_peak*1e3, y_peak, color='blue', zorder=7)
plt.text(
    x_peak*1e3 + 0.1,  # horizontal offset slightly more than other labels
    y_peak - 0.05,     # adjust vertical position as desired
    f"Peak: {x_peak*1e3:.2f} mm",
    fontsize=8
)

# Your chosen test point
x_test = 7.3e-3
y_test = np.interp(x_test, x_vals, B_reed_avg)
plt.scatter(x_test*1e3, y_test, color='green', zorder=7)
plt.text(
    x_test*1e3 + 0.3,   # horizontal offset
    y_test -0.03,      # vertical offset above the line
    f"Chosen Test Point: {x_test*1e3:.1f} mm",
    fontsize=8
)


plt.axvline(0, color='gray', linestyle='--', alpha=0.5)
plt.xlabel("Lateral offset x (mm)")
plt.ylabel("Normalized Bz")
plt.title("Point Field and Reed Threshold — Positive X Only")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.show()
