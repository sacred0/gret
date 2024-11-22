import bpy

module_names = [
    'helpers',
    'graft',  # So it appears at the top, fix this later
    'merge',
    'attribute_selection',
    'collision',
    'cut_faces_smooth',
    'extra_objects',
    'retarget_mesh',
    'sculpt_selection',
    'shape_key_apply_modifiers',
    'shape_key_encode',
    'shape_key_normalize',
    'shape_key_presets',
    'shape_key_select',
    'shape_key_clean',
    'shape_key_clean_all',
    'vertex_color_mapping',
    'vertex_group_bleed',
    'vertex_group_create_mirrored',
    'vertex_group_remove_unused',
    'vertex_group_smooth_loops',
]
from .. import import_or_reload_modules, register_submodules, unregister_submodules
modules = import_or_reload_modules(module_names, __name__)

class GRET_PT_mesh(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Mesh"

    draw_funcs = []

    @classmethod
    def poll(cls, context):
        return cls.bl_category and context.active_object and context.active_object.type == 'MESH'

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings, prefs):
    bpy.utils.register_class(GRET_PT_mesh)

    global registered_modules
    registered_modules = register_submodules(modules, settings, GRET_PT_mesh.draw_funcs)

def unregister():
    unregister_submodules(registered_modules, GRET_PT_mesh.draw_funcs)

    bpy.utils.unregister_class(GRET_PT_mesh)
