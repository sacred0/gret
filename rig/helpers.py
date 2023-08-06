from collections import namedtuple
from fnmatch import fnmatch
from itertools import chain
from mathutils import Vector, Quaternion, Euler
import bpy
import re

from ..patcher import FunctionPatcher
from ..helpers import intercept, get_context, select_only, titlecase
from ..log import log, logd

arp_export_module_names = [
    'auto_rig_pro.export_fbx.export_fbx_bin',
    'auto_rig_pro.src.export_fbx.export_fbx_bin',
]
arp_export_function_name = 'arp_save'
non_humanoid_bone_names = [
    'thigh_b_ref.l',
    'thigh_b_ref.r',
]
humanoid_bone_names = [
    'c_root_master.x',
]
limb_bone_names = [
    ('shoulder_ref.', 2),
    ('thigh_ref.', 2),
    ('neck_ref.', 1),
    ('ear_01_ref.', 2),
]
ik_bone_names = [
    "ik_foot_root",
    "ik_foot.l",
    "ik_foot.r",
    "ik_hand_root",
    "ik_hand_gun",
    "ik_hand.l",
    "ik_hand.r"
]
# Collect keys by calling gret.rig.helpers.collect_custom_values()
arp_default_pose_values = {
    'arm_twist': 0.0,
    'auto_eyelid': 0.1,
    'auto_stretch': 0.0,
    'autolips': None,  # Different values
    'bend_all': 0.0,
    'elbow_pin': 0.0,
    'eye_target': 1.0,
    'fingers_grasp': 0.0,
    'fix_roll': 0.0,
    'head_free': 0,
    'ik_fk_switch': 1.0,  # TODO should be configurable
    'ik_tip': 0,
    'leg_pin': 0.0,
    'lips_retain': 0.0,
    'lips_stretch': 1.0,
    'pole_parent': 1,
    'stretch_length': 1.0,
    'stretch_mode': 1,  # Bone original
    'thigh_twist': 0.0,
    'twist': 0.0,
    'volume_variation': 0.0,
    'y_scale': 2,  # Bone original
}
default_pose_values = {}
custom_prop_pattern = re.compile(r'(.+)?\["([^"]+)"\]')
prop_pattern = re.compile(r'(?:(.+)\.)?([^"\.]+)')

class PropertyWrapper(namedtuple('PrettyProperty', ['data', 'path', 'is_custom'])):
    # To set a property given a data path it's necessary to split the object and attribute name
    # `data.path_resolve(path, False)` returns a bpy_prop, and bpy_prop.data holds the object
    # Unfortunately bpy_prop knows the attribute name but doesn't expose it (see `bpy_prop.__str__`)
    # Also need to determine if it's a custom property, since those are accessed dict-like instead
    # For these reasons, resort to the less robust way of just parsing the data path with regexes

    @classmethod
    def from_path(cls, data, data_path):
        try:
            prop_match = custom_prop_pattern.fullmatch(data_path)
            if prop_match:
                if prop_match[1]:
                    data = data.path_resolve(prop_match[1])
                data_path = f'["{prop_match[2]}"]'
                data.path_resolve(data_path)  # Make sure the property exists
                return cls(data, data_path, True)

            prop_match = prop_pattern.fullmatch(data_path)
            if prop_match:
                if prop_match[1]:
                    data = data.path_resolve(prop_match[1])
                data_path = prop_match[2]
                data.path_resolve(data_path)  # Make sure the property exists
                return cls(data, data_path, False)
        except ValueError:
            return None

    @property
    def title(self):
        if self.is_custom:
            return titlecase(self.path[2:-2])  # Custom property name should be descriptive enough
        else:
            return f"{self.data.name} {titlecase(self.path)}"

    @property
    def default_value(self):
        if self.is_custom:
            return self.data.id_properties_ui(self.path[2:-2]).as_dict()['default']
        else:
            return self.data.bl_rna.properties[self.path].default

    @property
    def value(self):
        return self.data.path_resolve(self.path, True)

    @value.setter
    def value(self, new_value):
        if self.is_custom:
            self.data[self.path[2:-2]] = new_value
        else:
            setattr(self.data, self.path, new_value)

def is_object_arp(obj):
    """Returns whether the object is an Auto-Rig Pro armature."""
    return obj and obj.type == 'ARMATURE' and "c_pos" in obj.data.bones

def is_object_arp_humanoid(obj):
    """Returns whether the object is an Auto-Rig Pro humanoid armature."""
    # This is check_humanoid_limbs() from auto_rig_ge.py but less spaghetti
    if not is_object_arp(obj):
        return False

    if any(bname in obj.data.bones for bname in non_humanoid_bone_names):
        return False
    if not all(bname in obj.data.bones for bname in humanoid_bone_names):
        return False
    for limb_bone_name, max_bones in limb_bone_names:
        if sum(b.name.startswith(limb_bone_name) for b in obj.data.bones) > max_bones:
            return False
    if obj.rig_spine_count < 3:
        return False
    return True

def collect_custom_values():
    obj = bpy.context.object
    assert obj and obj.type == 'ARMATURE'
    prop_names = sorted(set(k for k, v in chain.from_iterable(pb.items() for pb in obj.pose.bones)
        if isinstance(v, (int, float))))
    return {k: sorted(set(pb[k] for pb in obj.pose.bones if k in pb)) for k in prop_names}

def clear_pose(obj, clear_gret_props=True, clear_armature_props=False, clear_bone_props=True):
    """Resets the given armature."""

    if not obj or obj.type != 'ARMATURE':
        return

    if clear_gret_props:
        for data_path in obj.get('properties', []):
            try:
                prop_wrapper = PropertyWrapper.from_path(obj, data_path)
                if prop_wrapper:
                    prop_wrapper.value = prop_wrapper.default_value
            except Exception as e:
                logd(f"Couldn't clear property \"{data_path}\": {e}")

    if clear_armature_props:
        for prop_name, prop_value in obj.items():
            if isinstance(prop_value, float):
                obj[prop_name] = 0.0

    is_arp = is_object_arp(obj)
    for pose_bone in obj.pose.bones:
        if clear_bone_props:
            for prop_name, prop_value in pose_bone.items():
                if is_arp and prop_name in arp_default_pose_values:
                    value = arp_default_pose_values[prop_name]
                    if value is not None:
                        pose_bone[prop_name] = value
                elif prop_name in default_pose_values:
                    value = default_pose_values[prop_name]
                    if value is not None:
                        pose_bone[prop_name] = value
                elif prop_name.startswith("_"):
                    continue
                else:
                    try:
                        pose_bone[prop_name] = type(prop_value)()
                    except TypeError:
                        pass
        pose_bone.location = Vector()
        pose_bone.rotation_quaternion = Quaternion()
        pose_bone.rotation_euler = Euler()
        pose_bone.rotation_axis_angle = [0.0, 0.0, 1.0, 0.0]
        pose_bone.scale = Vector((1.0, 1.0, 1.0))

def try_key(obj, prop_path, frame=0):
    try:
        return obj.keyframe_insert(prop_path, frame=frame)
    except TypeError:
        return False

def copy_drivers(src, dst, overwrite=False):
    """Copies drivers. src and dst should be of type bpy.types.ID with an AnimData slot."""

    if src and src.animation_data and dst:
        src_name = src.user.name if src.user else src.name
        for src_fc in src.animation_data.drivers:
            try:
                dst.path_resolve(src_fc.data_path)
            except ValueError:
                logd(f"Won't copy driver {src_fc.data_path} from {src_name}")
                continue
            if dst.animation_data is None:
                dst.animation_data_create()
            dst_drivers = dst.animation_data.drivers
            existing_fc = next((fc for fc in dst_drivers if fc.data_path == src_fc.data_path), None)
            if existing_fc and overwrite:
                dst_drivers.remove(existing_fc)
                existing_fc = None
            if not existing_fc:
                dst_drivers.from_existing(src_driver=src_fc)
                logd(f"Copied driver for {src_fc.data_path} from {src_name}")

def unmark_bones(rig, bone_names):
    num_deform = sum(b.use_deform for b in rig.data.bones)
    for bone in rig.data.bones:
        if bone.use_deform and any(fnmatch(bone.name, s) for s in bone_names):
            bone.use_deform = False
            for child_bone in bone.children_recursive:
                if child_bone.use_deform:
                    child_bone.use_deform = False
    num_unmarked = num_deform - sum(b.use_deform for b in rig.data.bones)
    if num_unmarked > 0:
        log(f"{num_unmarked} additional bone{'s' if num_unmarked > 1 else ''} won't be exported")

def unmark_unused_bones(rig, objs):
    """Unmarks deform for all bones that aren't relevant to the given meshes."""

    bones = rig.data.bones
    for bone in bones:
        bone.use_deform = False
    vgroup_names = set()
    for obj in objs:
        if obj.type == 'MESH':
            vgroup_names.update(vg.name for vg in obj.vertex_groups)
    num_deform = 0
    for vgroup_name in vgroup_names:
        bone = bones.get(vgroup_name)
        while bone:
            if not bone.use_deform:
                num_deform += 1
                bone.use_deform = True
            bone = bone.parent
    log(f"{num_deform} bones out of {len(bones)} marked for export")

def arp_save(base, *args, **kwargs):
    options = kwargs.pop('options')
    op, context = args
    logd(f"arp_save overriden with options: {options}")
    if options.get('minimize_bones'):
        unmark_unused_bones(context.active_object, context.selected_objects)
    remove_bone_names = options.get('remove_bones', [])
    if remove_bone_names:
        unmark_bones(context.active_object, remove_bone_names)
    return base(*args, **kwargs)

@intercept(error_result={'CANCELLED'})
def export_autorig(filepath, context, rig, objects=[], action=None, options={}):
    scn = context.scene

    ik_bones_not_found = [s for s in ik_bone_names if
        s not in rig.pose.bones or 'custom_bone' not in rig.pose.bones[s]]
    if not ik_bones_not_found:
        # All IK bones accounted for
        add_ik_bones = False
    elif len(ik_bones_not_found) == len(ik_bone_names):
        # No IK bones present, let ARP create them
        log("IK bones will be created")
        add_ik_bones = True
    else:
        # Only some IK bones found. Probably a mistake
        raise Exception("Some IK bones are missing or not marked for export: "
            + ", ".join(ik_bones_not_found))

    # Configure Auto-Rig and then finally export
    scn.arp_engine_type = 'unreal'
    scn.arp_export_rig_type = 'humanoid'
    scn.arp_ge_sel_only = True

    # Rig Definition
    scn.arp_keep_bend_bones = False
    scn.arp_push_bend = False
    scn.arp_full_facial = True
    scn.arp_export_twist = options.get('export_twist', True)
    scn.arp_export_noparent = False
    scn.arp_export_renaming = True  # Just prints a message if the file doesn't exist

    # Units
    scn.arp_units_x100 = True

    # Unreal Options
    scn.arp_ue4 = True
    scn.arp_ue_root_motion = True
    scn.arp_rename_for_ue = True
    scn.arp_ue_ik = add_ik_bones
    scn.arp_ue_ik_anim = True  # This only works with arp_ue_ik. I patched ARP to address this
    scn.arp_mannequin_axes = True

    # Animation
    if not action:
        scn.arp_bake_anim = False
    else:
        scn.arp_bake_anim = True
        scn.arp_bake_type = 'ACTIONS'
        scn.arp_export_separate_fbx = False
        scn.arp_frame_range_type = 'CUSTOM'
        if action.use_frame_range:
            scn.arp_export_start_frame = int(action.frame_start)
            scn.arp_export_end_frame = int(action.frame_end)
        else:
            scn.arp_export_start_frame = int(action.curve_frame_range[0])
            scn.arp_export_end_frame = int(action.curve_frame_range[1])
        scn.arp_export_act_name = 'DEFAULT'
        scn.arp_simplify_fac = 0.0
        scn.arp_export_use_actlist = True
        scn.arp_export_actlist.clear()
        arp_actlist = scn.arp_export_actlist.add()
        arp_action = arp_actlist.actions.add()
        arp_action.action = action

    # Misc
    scn.arp_global_scale = 1.0
    scn.arp_mesh_smooth_type = 'EDGE'
    scn.arp_use_tspace = False
    scn.arp_fix_fbx_rot = True
    scn.arp_fix_fbx_matrix = True
    scn.arp_init_fbx_rot = False
    scn.arp_init_fbx_rot_mesh = False
    scn.arp_bone_axis_primary_export = 'Y'
    scn.arp_bone_axis_secondary_export = 'X'
    scn.arp_export_rig_name = 'root'
    scn.arp_export_tex = False

    rig.data.pose_position = 'POSE'
    clear_pose(rig)

    # ARP doesn't respect context unfortunately
    select_only(context, objects)
    rig.select_set(True)
    context.view_layer.objects.active = rig
    with FunctionPatcher(arp_export_module_names, arp_export_function_name, arp_save) as patcher:
        patcher['options'] = options
        return bpy.ops.id.arp_export_fbx_panel(filepath=filepath)

@intercept(error_result={'CANCELLED'})
def export_autorig_universal(filepath, context, rig, objects=[], action=None, options={}):
    scn = context.scene

    # Configure Auto-Rig and then finally export
    scn.arp_engine_type = 'unreal'
    scn.arp_export_rig_type = 'mped'
    scn.arp_ge_sel_only = True

    # Rig Definition
    scn.arp_keep_bend_bones = False
    scn.arp_push_bend = False
    scn.arp_export_twist = options.get('export_twist', True)
    scn.arp_export_noparent = False
    scn.arp_export_renaming = True  # Just prints a message if the file doesn't exist

    # Units
    scn.arp_units_x100 = True

    # Unreal Options
    scn.arp_ue_root_motion = True

    # Animation
    if not action:
        scn.arp_bake_anim = False
    else:
        scn.arp_bake_anim = True
        scn.arp_bake_type = 'ACTIONS'
        scn.arp_export_separate_fbx = False
        scn.arp_frame_range_type = 'CUSTOM'
        if action.use_frame_range:
            scn.arp_export_start_frame = int(action.frame_start)
            scn.arp_export_end_frame = int(action.frame_end)
        else:
            scn.arp_export_start_frame = int(action.curve_frame_range[0])
            scn.arp_export_end_frame = int(action.curve_frame_range[1])
        scn.arp_export_act_name = 'DEFAULT'
        scn.arp_simplify_fac = 0.0
        scn.arp_export_use_actlist = True
        scn.arp_export_actlist.clear()
        arp_actlist = scn.arp_export_actlist.add()
        arp_action = arp_actlist.actions.add()
        arp_action.action = action

    # Misc
    scn.arp_global_scale = 1.0
    scn.arp_mesh_smooth_type = 'EDGE'
    scn.arp_use_tspace = False
    scn.arp_fix_fbx_rot = True
    scn.arp_fix_fbx_matrix = True
    scn.arp_init_fbx_rot = False
    scn.arp_init_fbx_rot_mesh = False
    scn.arp_bone_axis_primary_export = 'Y'
    scn.arp_bone_axis_secondary_export = 'X'
    scn.arp_export_rig_name = 'root'
    scn.arp_export_tex = False

    rig.data.pose_position = 'POSE'
    clear_pose(rig)

    # ARP doesn't respect context unfortunately
    select_only(context, objects)
    rig.select_set(True)
    context.view_layer.objects.active = rig
    with FunctionPatcher(arp_export_module_names, arp_export_function_name, arp_save) as patcher:
        patcher['options'] = options
        return bpy.ops.id.arp_export_fbx_panel(filepath=filepath)

@intercept(error_result={'CANCELLED'})
def export_fbx(filepath, context, rig, objects=[], action=None, options={}):
    if action:
        # TODO Put action in the timeline
        # rig.animation_data.action = action
        # context.scene.frame_preview_start = int(action.frame_start)
        # context.scene.frame_preview_end = int(action.frame_end)
        # context.scene.use_preview_range = True
        # context.scene.frame_current = context.scene.frame_preview_start
        raise NotImplementedError

    # Temporarily rename the armature since it will become the root bone
    root_bone_name = "root"
    existing_obj_named_root = bpy.data.objects.get(root_bone_name)
    if existing_obj_named_root:
        existing_obj_named_root.name = root_bone_name + "_"
    saved_rig_name = rig.name
    rig.name = root_bone_name
    rig.data.pose_position = 'POSE'
    clear_pose(rig)

    if options.get('minimize_bones'):
        unmark_unused_bones(rig, objects)
    remove_bone_names = options.get('remove_bones', [])
    if remove_bone_names:
        unmark_bones(rig, remove_bone_names)

    select_only(context, objects)
    rig.select_set(True)
    context.view_layer.objects.active = rig
    result = bpy.ops.export_scene.fbx(
        filepath=filepath
        , check_existing=False
        , use_selection=True
        , use_visible=False
        , use_active_collection=False
        , global_scale=1.0
        , apply_unit_scale=True
        , apply_scale_options='FBX_SCALE_NONE'
        , use_space_transform=True
        , bake_space_transform=True
        , object_types={'ARMATURE', 'MESH'}
        , use_mesh_modifiers=True
        , use_mesh_modifiers_render=False
        , mesh_smooth_type='EDGE'
        , colors_type='SRGB'
        , prioritize_active_color=False
        , use_subsurf=False
        , use_mesh_edges=False
        , use_tspace=False
        , use_triangles=False
        , use_custom_props=False
        , add_leaf_bones=False
        , primary_bone_axis='Y'
        , secondary_bone_axis='X'
        , use_armature_deform_only=True
        , armature_nodetype='NULL'
        , bake_anim=action is not None
        , bake_anim_use_all_bones=False
        , bake_anim_use_nla_strips=False
        , bake_anim_use_all_actions=True
        , bake_anim_force_startend_keying=True
        , bake_anim_step=1
        , bake_anim_simplify_factor=1
        , path_mode='STRIP'
        , embed_textures=False
        , batch_mode='OFF'
        , use_batch_own_dir=False
        , use_metadata=False
        , axis_forward='-Z'
        , axis_up='Y'
    )

    # Clean up
    rig.name = saved_rig_name
    if existing_obj_named_root:
        existing_obj_named_root.name = root_bone_name

    return result
