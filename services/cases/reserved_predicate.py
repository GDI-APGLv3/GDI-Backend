
from typing import Final


def build_reserved_or_exists(case_ref: str, user_ph: str) -> str:
    return f"""EXISTS (
    SELECT 1 FROM case_responsibles cr
    WHERE cr.case_id = {case_ref} AND cr.user_id = {user_ph} AND cr.is_active = true
)
OR EXISTS (
    -- R2: titular directo del departamento del sector administrador actual
    SELECT 1 FROM case_movements cm
    JOIN sectors s ON s.id = cm.admin_sector_id
    JOIN departments d ON d.id = s.department_id
    WHERE cm.case_id = {case_ref} AND cm.is_active = false
      AND cm.type IN ('creation','transfer')
      AND cm.closed_at = (
          SELECT MAX(cm2.closed_at) FROM case_movements cm2
          WHERE cm2.case_id = {case_ref} AND cm2.type IN ('creation','transfer') AND cm2.is_active = false
      )
      AND d.head_user_id = {user_ph}
)
OR EXISTS (
    -- R3: titular directo del departamento de cada sector asignado activo
    SELECT 1 FROM case_movements cm
    JOIN sectors s ON s.id = cm.assigned_sector_id
    JOIN departments d ON d.id = s.department_id
    WHERE cm.case_id = {case_ref} AND cm.is_active = true
      AND cm.assigned_sector_id IS NOT NULL
      AND d.head_user_id = {user_ph}
)
OR EXISTS (
    -- R4 (GDI-069 fix 07/07): actuante con tarea de asignacion ABIERTA
    SELECT 1 FROM case_assignment_tasks cat
    WHERE cat.case_id = {case_ref}
      AND cat.assigned_user_id = {user_ph}
      AND cat.status = 'open'
)"""


RESERVED_BRANCH_SUBSTRINGS: Final = (
    "case_responsibles cr",
    "cr.is_active = true",
    "cm.admin_sector_id",
    "cm.assigned_sector_id",
    "d.head_user_id",
    "case_assignment_tasks cat",
    "cat.assigned_user_id",
    "cat.status = 'open'",
)
