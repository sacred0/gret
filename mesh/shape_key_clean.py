import bpy
import bmesh

from .helpers import edit_mesh_elements
from ..math import get_dist_sq

class GRET_OT_shape_key_clean(bpy.types.Operator):
    """Select vertices affected by the current shape key"""

    bl_idname = 'gret.shape_key_clean'
    bl_label = "Clean Shape Key"
    bl_context = 'objectmode'
    bl_options = {'REGISTER', 'UNDO'}

    distance: bpy.props.FloatProperty(
        name="Distance",
        description="Minimum delta distance to select",
        subtype='DISTANCE',
        default=0,
        min=0.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.active_shape_key_index > 0

    def execute(self, context):
        obj = context.active_object
        sk_data = obj.active_shape_key.data
        threshold = 0.00000001

        num_selected = 0

        for v in obj.data.vertices:
            if get_dist_sq(v.co, sk_data[v.index].co) < threshold:
                sk_data[v.index].co = v.co
                num_selected += 1

        if num_selected > 0:
            self.report({'INFO'}, f"Cleaned {num_selected} vertices.")
        else:
            self.report({'INFO'}, f"No vertices were modified.")

        obj.data.update()

        return {'FINISHED'}


def draw_menu(self, context):
    self.layout.operator(GRET_OT_shape_key_clean.bl_idname)

def register(settings, prefs):
    if not prefs.mesh__enable_shape_key_select:
        return False

    bpy.utils.register_class(GRET_OT_shape_key_clean)
    bpy.types.MESH_MT_shape_key_context_menu.append(draw_menu)

def unregister():
    bpy.types.MESH_MT_shape_key_context_menu.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_shape_key_clean)
