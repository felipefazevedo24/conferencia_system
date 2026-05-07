"""
One-time script: rebuild facilities_admin.html correctly.
Removes the orphan HTML+duplicate-JS block that ended up inside <script>.
Run: python fix_admin_template.py
"""
import re, shutil, os

src = "templates/facilities_admin.html"
shutil.copy(src, src + ".bak")  # safety backup

with open(src, encoding="utf-8") as f:
    content = f.read()

# ── 1. Remove the garbage HTML (old panels + old modals) that are inside
#       the new <script> block.  These are lines 841-1143 originally.
#       We detect them as: everything between
#         setInterval(updateBadges, 60000);\n\n\n  <junk html>
#       and the {% endblock %} marker.
# Strategy: find the exact garbage region and delete it.

start_marker = "setInterval(updateBadges, 60000);\n"
end_marker   = "{% endblock %}\n\n{% block extra_js %}\n<script>\n"

start_idx = content.find(start_marker)
end_idx   = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print("ERROR: Could not find markers. Aborting.")
    print(f"  start_marker found: {start_idx != -1}")
    print(f"  end_marker found:   {end_idx != -1}")
    exit(1)

# Everything after start_marker up to (and including) end_marker needs to go
garbage_start = start_idx + len(start_marker)
garbage_end   = end_idx + len(end_marker)

# Build: keep everything up to start_marker (inclusive),
#        then the loadColaboradores function we need,
#        then skip to just after end_marker.
load_colabs_fn = """
// ─── COLABORADORES ──────────────────────────────────────────────────────────
async function loadColaboradores() {
  const r = await fetch('/api/facilities/colaboradores');
  const d = await r.json();
  colaboradores = d.rows || [];
  const selLimpeza = document.getElementById('limpezaColaborador');
  if (selLimpeza) {
    selLimpeza.innerHTML = '<option value="">-- Selecione --</option>';
    colaboradores.forEach(c => {
      selLimpeza.innerHTML += `<option value="${c.id}">${c.nome}</option>`;
    });
  }
}

"""

new_content = (
    content[:garbage_start]          # end with setInterval(updateBadges…)\n
    + load_colabs_fn
    + content[garbage_end:]          # continues from // ========== EPI ==========
)

# ── 2. Remove the old duplicate init / loadDashboard / setInterval / switchTab
#       block that was left from the original {% block extra_js %} section.
#       These functions appear again starting with 'let _debounceTimer' (EPI).
#       The block to remove is from 'let colaboradores = [];' through
#       '}\n\n// ========== EPI ==========' (exclusive of the EPI comment).

dup_start = "let colaboradores = [];\n\nasync function init() {"
dup_end   = "}\n\n// ========== EPI =========="

dup_si = new_content.find(dup_start)
dup_ei = new_content.find(dup_end)

if dup_si != -1 and dup_ei != -1 and dup_si < dup_ei:
    # Remove from dup_start to just before '// ========== EPI =========='
    new_content = new_content[:dup_si] + new_content[dup_ei + len("}\n\n"):]
    print("✓ Removed duplicate JS block (init/loadDashboard/switchTab)")
else:
    print(f"  dup block markers: start={dup_si}, end={dup_ei}")
    print("  Note: duplicate JS block not found (may already be clean)")

# ── 3. Fix the final {% endblock %} → </script></body></html>
new_content = new_content.replace(
    "init();\n</script>\n{% endblock %}",
    "init();\n</script>\n</body>\n</html>"
)

with open(src, "w", encoding="utf-8") as f:
    f.write(new_content)

total = new_content.count("\n") + 1
print(f"✓ Done. File now has {total} lines.")
print(f"✓ Backup saved to {src}.bak")
