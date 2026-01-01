import numpy as np
import matplotlib.pyplot as plt
from scipy.special import ellipk, ellipe

# Magnet geometry
R = 3e-3
L = 3e-3
z0 = 2.5e-3
M = 1e6
mu0 = 4*np.pi*1e-7

def Bz_exact(r, z):
    def safe_ellipk(m):
        return ellipk(m) if m < 1 else ellipk(0.999999999)
    ztp = z - (+L/2)
    zbp = z - (-L/2)
    def f_component(r, zp):
        denom = (R + r)**2 + zp**2
        m = 4*R*r / denom if r != 0 else 0.0
        K = safe_ellipk(m)
        E = ellipe(m)
        term1 = ((R - r) / np.sqrt((R - r)**2 + zp**2)) * E if r != R else 0
        term2 = (R + r) / np.sqrt((R + r)**2 + zp**2)
        return term1 - term2 * E + zp * K
    return (mu0 * M / np.pi) * (f_component(r, ztp) - f_component(r, zbp))

# X-axis offsets
x_vals = np.linspace(0, 20e-3, 5000)  # high resolution
Bz_vals = np.array([Bz_exact(x, z0) for x in x_vals])

# Threshold magnitude based on experimental 15mm point
x_ref = 15e-3
threshold = np.interp(x_ref, x_vals, np.abs(Bz_vals))

# Find all crossings using interpolation
crossings = []
for i in range(len(x_vals)-1):
    if (np.abs(Bz_vals[i]) - threshold) * (np.abs(Bz_vals[i+1]) - threshold) < 0:
        # linear interpolation to get exact x
        x_cross = x_vals[i] + (threshold - np.abs(Bz_vals[i])) / (np.abs(Bz_vals[i+1]) - np.abs(Bz_vals[i])) * (x_vals[i+1] - x_vals[i])
        y_cross = np.interp(x_cross, x_vals, Bz_vals)
        crossings.append((x_cross, y_cross))

# Include the original 15mm reference explicitly
y_ref = np.interp(x_ref, x_vals, Bz_vals)
crossings.append((x_ref, y_ref))

# Plot
plt.figure(figsize=(10,6))
plt.plot(x_vals*1e3, Bz_vals, label='Bz(x)')  

# Mark all threshold points
for x_pt, y_pt in crossings:
    plt.scatter(x_pt*1e3, y_pt, color='red', zorder=5)
    plt.text(x_pt*1e3+0.3, y_pt, f'x={x_pt*1e3:.2f}mm\ny={y_pt:.3f}', ha='left', va='center', fontsize=8)

plt.xlabel("Lateral Offset x (mm)")
plt.ylabel("B_z (T)")
plt.title("Normalized Z Magnetic Field vs Horizontal Offset")
plt.grid(True)
plt.legend()
plt.show()
