"""Render an OBJ mesh to a PNG from a fixed isometric camera. argv: obj png title"""
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

verts, faces = [], []
for line in open(sys.argv[1], errors="replace"):
    if line.startswith("v "):
        verts.append([float(x) for x in line.split()[1:4]])
    elif line.startswith("f "):
        idx = [int(t.split("/")[0]) for t in line.split()[1:]]
        faces.append([i - 1 if i > 0 else len(verts) + i for i in idx])

V = np.asarray(verts, float)
polys = [V[f] for f in faces if len(f) >= 3]
if not len(V) or not polys:
    sys.exit("empty mesh")

fig = plt.figure(figsize=(9, 7), dpi=110)
ax = fig.add_subplot(111, projection="3d")
ax.add_collection3d(Poly3DCollection(polys, facecolor="#8fb3d9", edgecolor="#22364a",
                                     linewidths=0.25))
lo, hi = V.min(0), V.max(0)
size = hi - lo
ctr = (lo + hi) / 2
span = size.max() / 2 or 1.0
for setlim, c in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), ctr):
    setlim(c - span, c + span)
ax.set_box_aspect((1, 1, 1))
ax.view_init(elev=26, azim=-58)
ax.set_xlabel("X")
ax.set_ylabel("Y")
ax.set_zlabel("Z")
ax.set_title("%s\n%.1f x %.1f x %.1f mm  |  %d verts / %d faces"
             % (sys.argv[3], size[0], size[1], size[2], len(V), len(polys)), fontsize=9)
fig.tight_layout()
fig.savefig(sys.argv[2])
print("bbox %.2f %.2f %.2f verts %d faces %d" % (size[0], size[1], size[2], len(V), len(polys)))
