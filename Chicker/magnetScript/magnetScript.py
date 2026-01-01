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
R = 3e-3      # radius (m)  -> 6 mm diameter
L = 3e-3      # thickness (m)
z0 = 2.5e-3   # sensor height above magnet center (m)

# ============================
# Spatial domain
# ============================
x_vals = np.linspace(0, 20e-3, 5000)

# ============================
# Elliptic-integral kernel
# ============================
def f_component(r, zp):
    denom = (R + r)**2 + zp**2
    m = 4 * R * r / denom if r != 0 else 0.0

    # numerical safety
    if m >= 1:
        m = 0.999999999

    K = ellipk(m)
    E = ellipe(m)

    term1 = ((R - r) / np.sqrt((R - r)**2 + zp**2)) * E if r != R else 0.0
    term2 = (R + r) / np.sqrt((R + r)**2 + zp**2)

    return term1 - term2 * E + zp * K

# ============================
# Pole distances
# ============================
ztp = z0 - L / 2   # top face
zbp = z0 + L / 2   # bottom face

# ============================
# Field contributions
# ============================
B_top = np.array([
    mu0 * M / np.pi * f_component(x, ztp)
    for x in x_vals
])

B_bottom = np.array([
    mu0 * M / np.pi * f_component(x, zbp)
    for x in x_vals
])

# ============================
# Total field
# ============================
B_total = B_top - B_bottom

# ============================
# Proper normalization
# ============================
B_max = np.max(np.abs(B_total))
B_norm = B_total / B_max

print("Max |B_norm| =", np.max(np.abs(B_norm)))  # should be ~1

# ============================
# Plot base field
# ============================
plt.figure(figsize=(10, 6))
plt.plot(x_vals * 1e3, B_norm, label="Bz(x)")

# ============================
# Threshold based on x = 15 mm
# ============================
x_ref = 15e-3
B_ref = np.interp(x_ref, x_vals, np.abs(B_norm))

# ============================
# Find all |B| = threshold crossings
# ============================
crossings = []

for i in range(len(x_vals) - 1):
    y1 = np.abs(B_norm[i]) - B_ref
    y2 = np.abs(B_norm[i + 1]) - B_ref

    if y1 * y2 < 0:
        x_cross = x_vals[i] + (0 - y1) * (x_vals[i+1] - x_vals[i]) / (y2 - y1)
        y_cross = np.interp(x_cross, x_vals, B_norm)
        crossings.append((x_cross, y_cross))

# explicitly include the 15 mm reference point
crossings.append((x_ref, np.interp(x_ref, x_vals, B_norm)))

# ============================
# Plot labels and markers
# ============================
for x_pt, y_pt in crossings:
    plt.scatter(x_pt * 1e3, y_pt, color='red', zorder=6)
    plt.text(
        x_pt * 1e3 + 0.3,
        y_pt,
        f"x = {x_pt*1e3:.2f} mm\nB = {y_pt:.3f}",
        fontsize=8,
        verticalalignment='center'
    )

# ============================
# Final plot formatting
# ============================
plt.xlabel("Lateral offset x (mm)")
plt.ylabel("Normalized Bz")
plt.title("Normalized Magnetic Field vs Lateral Offset")
plt.grid(True)
plt.legend()
plt.show()
