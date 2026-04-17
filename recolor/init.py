import json
import os
import re
import shutil

import substance_painter.export
import substance_painter.js
import substance_painter.layerstack
import substance_painter.logging
import substance_painter.project
import substance_painter.textureset
import substance_painter.ui
from PySide6 import QtCore, QtGui, QtWidgets

BASE_DIR = os.path.dirname(__file__)
PRESET_FILE = os.path.join(BASE_DIR, 'quick_colors.json')
SETTINGS_FILE = os.path.join(BASE_DIR, 'plugin_settings.json')

plugin_widgets = []
tool_widget = None
tool_dock = None
color_hex_label = None
color_swatch_label = None
export_dir_line_edit = None
project_dir_line_edit = None
manual_color_line_edit = None
quick_color_buttons = []
quick_color_presets = []
current_color_value = None
current_color_rgb = (30, 144, 255)
active_color_dialog = None
active_color_dialog_filter = None
last_export_dir = ''
last_project_dir = ''


def default_quick_colors():
    return [
        [255, 255, 255], [0, 0, 0], [255, 0, 0], [255, 128, 0], [255, 230, 0], [180, 255, 0],
        [0, 200, 80], [0, 255, 255], [0, 120, 255], [60, 0, 255], [180, 0, 255], [255, 0, 180],
    ]


def clamp_channel(value):
    value = max(0.0, min(1.0, float(value)))
    return int(round(value * 255.0))


def rgb_to_hex(rgb):
    return '#{0:02X}{1:02X}{2:02X}'.format(*rgb)


def normalize_hex_color(text):
    text = text.strip()
    if not text:
        raise ValueError('empty color')
    if not text.startswith('#'):
        text = '#' + text
    if len(text) != 7:
        raise ValueError('hex color must be 6 digits')
    int(text[1:], 16)
    return text.upper()


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception:
        return default


def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=True, indent=2)


def load_quick_colors():
    global quick_color_presets
    data = load_json(PRESET_FILE, [])
    parsed = []
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            parsed.append([int(item[0]), int(item[1]), int(item[2])])
    quick_color_presets = parsed or default_quick_colors()
    if len(quick_color_presets) < len(default_quick_colors()):
        quick_color_presets.extend(default_quick_colors()[len(quick_color_presets):])


def save_quick_colors():
    save_json(PRESET_FILE, quick_color_presets)


def load_plugin_settings():
    global last_export_dir
    global last_project_dir
    data = load_json(SETTINGS_FILE, {})
    last_export_dir = str(data.get('last_export_dir', '') or '')
    last_project_dir = str(data.get('last_project_dir', '') or '')


def save_plugin_settings():
    save_json(
        SETTINGS_FILE,
        {
            'last_export_dir': last_export_dir,
            'last_project_dir': last_project_dir,
        }
    )


def get_node_uid(node):
    uid_value = getattr(node, 'uid', None)
    return uid_value() if callable(uid_value) else uid_value


def color_to_rgb255(color_value):
    if color_value is None:
        return current_color_rgb

    srgb_attr = getattr(color_value, 'sRGB', None)
    if callable(srgb_attr):
        try:
            values = list(srgb_attr())
            if len(values) >= 3:
                return tuple(clamp_channel(value) for value in values[:3])
        except Exception:
            pass
    elif srgb_attr is not None:
        try:
            values = list(srgb_attr)
            if len(values) >= 3:
                return tuple(clamp_channel(value) for value in values[:3])
        except Exception:
            pass

    for names in (('r', 'g', 'b'), ('red', 'green', 'blue')):
        if all(hasattr(color_value, name) for name in names):
            return tuple(clamp_channel(getattr(color_value, name)) for name in names)

    if all(hasattr(color_value, name) for name in ('redF', 'greenF', 'blueF')):
        return (
            clamp_channel(color_value.redF()),
            clamp_channel(color_value.greenF()),
            clamp_channel(color_value.blueF()),
        )

    try:
        values = list(color_value)
        if len(values) >= 3:
            return tuple(clamp_channel(value) for value in values[:3])
    except TypeError:
        pass

    return current_color_rgb


def build_color_candidates(rgb255):
    rgb01 = [channel / 255.0 for channel in rgb255]
    candidates = []
    if current_color_value is not None:
        candidates.append(current_color_value)
    color_type = getattr(substance_painter.layerstack, 'Color', None)
    if color_type is not None:
        try:
            candidates.append(color_type(*rgb01))
        except Exception:
            pass
        try:
            candidates.append(color_type(rgb01[0], rgb01[1], rgb01[2], 1.0))
        except Exception:
            pass
    candidates.append(rgb01)
    candidates.append(tuple(rgb01))
    return candidates


def refresh_quick_color_buttons():
    for index, button in enumerate(quick_color_buttons):
        if index >= len(quick_color_presets):
            continue
        rgb = tuple(quick_color_presets[index])
        button.setStyleSheet(
            'background-color: {0}; border: 1px solid #666; border-radius: 2px; '
            'padding: 0px; margin: 0px; min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px;'.format(
                rgb_to_hex(rgb)
            )
        )
        button.setToolTip('{0}\nLeft click apply\nRight click save current color'.format(rgb_to_hex(rgb)))


def update_color_preview():
    current_hex = rgb_to_hex(current_color_rgb)
    if color_hex_label is not None:
        color_hex_label.setText(current_hex)
    if color_swatch_label is not None:
        color_swatch_label.setStyleSheet(
            'background-color: {0}; border: 1px solid #666; border-radius: 4px;'.format(current_hex)
        )
    if manual_color_line_edit is not None and manual_color_line_edit.text() != current_hex:
        manual_color_line_edit.setText(current_hex)
    refresh_quick_color_buttons()


def set_current_color_rgb(rgb):
    global current_color_value
    global current_color_rgb
    current_color_rgb = tuple(int(channel) for channel in rgb)
    current_color_value = None
    update_color_preview()


def try_activate_qt_eyedropper(dialog):
    keywords = ('pick screen color', 'screen color', 'eyedropper', '吸管', '取色', '屏幕')
    for button in dialog.findChildren(QtWidgets.QAbstractButton):
        texts = [
            button.text() or '', button.toolTip() or '', button.statusTip() or '',
            button.whatsThis() or '', button.accessibleName() or '',
            button.accessibleDescription() or '', button.objectName() or '',
        ]
        if any(keyword in ' '.join(texts).lower() for keyword in keywords):
            button.click()
            return True
    return False


def open_color_dialog():
    global active_color_dialog
    global active_color_dialog_filter

    initial = QtGui.QColor(*current_color_rgb)
    dialog = QtWidgets.QColorDialog(initial, None)
    dialog.setWindowTitle('\u9009\u62e9\u989c\u8272')
    dialog.setOption(QtWidgets.QColorDialog.DontUseNativeDialog, True)
    dialog.setOption(QtWidgets.QColorDialog.ShowAlphaChannel, False)
    active_color_dialog = dialog
    state = {'armed': False, 'pending_rgb': None}

    class EyeDropperReleaseFilter(QtCore.QObject):
        def eventFilter(self, watched, event):
            if event.type() == QtCore.QEvent.MouseButtonRelease and state['armed'] and state['pending_rgb'] is not None:
                set_current_color_rgb(state['pending_rgb'])
                substance_painter.logging.info('\u5df2\u5438\u53d6\u989c\u8272\uff1a' + rgb_to_hex(current_color_rgb))
                dialog.done(QtWidgets.QDialog.Accepted)
                dialog.close()
            return False

    active_color_dialog_filter = EyeDropperReleaseFilter()
    QtWidgets.QApplication.instance().installEventFilter(active_color_dialog_filter)

    def handle_current_color_changed(selected):
        state['pending_rgb'] = (selected.red(), selected.green(), selected.blue())

    def handle_dialog_finished(_result):
        global active_color_dialog
        global active_color_dialog_filter
        app = QtWidgets.QApplication.instance()
        if active_color_dialog_filter is not None:
            app.removeEventFilter(active_color_dialog_filter)
            active_color_dialog_filter = None
        active_color_dialog = None

    dialog.currentColorChanged.connect(handle_current_color_changed)
    dialog.finished.connect(handle_dialog_finished)
    QtCore.QTimer.singleShot(0, lambda: try_activate_qt_eyedropper(dialog))
    QtCore.QTimer.singleShot(200, lambda: state.__setitem__('armed', True))
    dialog.open()


def get_active_stack():
    if not substance_painter.project.is_open():
        substance_painter.logging.warning('\u8bf7\u5148\u6253\u5f00\u4e00\u4e2a Painter \u5de5\u7a0b\u3002')
        return None
    stack = substance_painter.textureset.get_active_stack()
    if stack is None:
        substance_painter.logging.warning('\u5f53\u524d\u6ca1\u6709\u6fc0\u6d3b\u7684 Texture Set\u3002')
        return None
    return stack


def get_selected_fill_layer_sources():
    try:
        stack = get_active_stack()
        if stack is None:
            return []
        sources = []
        nodes = substance_painter.layerstack.get_selected_nodes(stack)
        for node in nodes:
            if node.get_type() != substance_painter.layerstack.NodeType.FillLayer:
                continue
            try:
                sources.append(node.get_source(substance_painter.layerstack.ChannelType.BaseColor))
            except Exception as exc:
                substance_painter.logging.warning('\u8bfb\u53d6 Fill Layer BaseColor \u5931\u8d25\uff1a' + str(exc))
        if not sources:
            substance_painter.logging.warning('\u8bf7\u5148\u9009\u4e2d\u4e00\u4e2a\u6216\u591a\u4e2a Fill Layer\u3002')
        return sources
    except Exception as exc:
        substance_painter.logging.warning('\u8bfb\u53d6\u9009\u4e2d Fill Layer \u5931\u8d25\uff1a' + str(exc))
        return []


def load_selected_fill_layer_color():
    global current_color_value
    global current_color_rgb
    try:
        sources = get_selected_fill_layer_sources()
        if not sources:
            return
        source = sources[0]
        color_getter = getattr(source, 'get_color', None)
        color_value = color_getter() if callable(color_getter) else getattr(source, 'color', None)
        if color_value is None:
            raise RuntimeError('Cannot read the current Fill Layer BaseColor.')
        current_color_value = color_value
        current_color_rgb = color_to_rgb255(color_value)
        update_color_preview()
        substance_painter.logging.info('\u5df2\u8bfb\u53d6 BaseColor\uff1a' + rgb_to_hex(current_color_rgb))
    except Exception as exc:
        substance_painter.logging.warning('\u8bfb\u53d6\u5f53\u524d\u989c\u8272\u5931\u8d25\uff1a' + str(exc))


def apply_current_color_to_selected_fill_layers():
    try:
        sources = get_selected_fill_layer_sources()
        if not sources:
            return
        changed_count = 0
        candidates = build_color_candidates(current_color_rgb)
        for source in sources:
            applied = False
            for candidate in candidates:
                try:
                    source.set_color(candidate)
                    applied = True
                    changed_count += 1
                    break
                except Exception:
                    continue
            if not applied:
                substance_painter.logging.warning('\u6709\u4e00\u4e2a Fill Layer \u989c\u8272\u5199\u5165\u5931\u8d25\u3002')
        if changed_count:
            substance_painter.logging.info(
                '\u5df2\u628a {0} \u5e94\u7528\u5230 {1} \u4e2a Fill Layer\u3002'.format(
                    rgb_to_hex(current_color_rgb), changed_count
                )
            )
        else:
            substance_painter.logging.warning('\u6ca1\u6709\u6210\u529f\u66f4\u65b0\u4efb\u4f55 Fill Layer\u3002')
    except Exception as exc:
        substance_painter.logging.warning('\u5e94\u7528\u5f53\u524d\u989c\u8272\u5931\u8d25\uff1a' + str(exc))


def set_current_color_from_text():
    if manual_color_line_edit is None:
        return
    try:
        hex_value = normalize_hex_color(manual_color_line_edit.text())
        set_current_color_rgb(tuple(int(hex_value[i:i + 2], 16) for i in (1, 3, 5)))
        substance_painter.logging.info('\u5f53\u524d\u989c\u8272\u5df2\u8bbe\u7f6e\u4e3a\uff1a' + hex_value)
    except Exception:
        substance_painter.logging.warning('\u8bf7\u8f93\u5165\u7c7b\u4f3c #FF6600 \u7684\u5341\u516d\u8fdb\u5236\u989c\u8272\u3002')


def make_quick_color_setter(index):
    def handler():
        rgb = tuple(quick_color_presets[index])
        set_current_color_rgb(rgb)
        substance_painter.logging.info('Quick color: ' + rgb_to_hex(rgb))
    return handler


def save_quick_color(index):
    quick_color_presets[index] = [current_color_rgb[0], current_color_rgb[1], current_color_rgb[2]]
    save_quick_colors()
    refresh_quick_color_buttons()
    substance_painter.logging.info('Saved quick color {0}: {1}'.format(index + 1, rgb_to_hex(current_color_rgb)))


class QuickColorButton(QtWidgets.QPushButton):
    def __init__(self, index):
        super().__init__()
        self.index = index
        self.setFixedSize(14, 14)
        self.setStyleSheet('padding: 0px; margin: 0px;')
        self.clicked.connect(make_quick_color_setter(index))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.RightButton:
            save_quick_color(self.index)
            event.accept()
            return
        super().mousePressEvent(event)


def get_material_name(stack):
    material = stack.material()
    if hasattr(material, 'name'):
        try:
            return material.name()
        except TypeError:
            try:
                return material.name
            except Exception:
                pass
    material_name = str(material)
    if '/' in material_name:
        material_name = material_name.split('/', 1)[0]
    return material_name


def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]+', '_', name)
    name = re.sub(r'\s+', '_', name).strip('._ ')
    return name or 'Export'


def build_export_basename(material_name):
    if material_name.startswith('ALP_Mat_'):
        return 'ALP_Tx_' + material_name[len('ALP_Mat_'):]
    return 'ALP_Tx_' + material_name


def normalize_folder_name(name):
    return re.sub(r'[\s_-]+', '', str(name or '')).lower()


def classify_export_folder(name):
    normalized = normalize_folder_name(name)
    if normalized in (
        '\u5149\u7167\u4fe1\u606f', 'lighting', 'lightinginfo', 'lightinfo',
        'basebycj\u5149\u7167'
    ):
        return 'lighting'
    if normalized in (
        'id', 'id\u901a\u9053', 'idchannel', 'paletteindex', '\u8c03\u8272id', '\u8c03\u8272\u901a\u9053',
        'basebycjid'
    ):
        return 'palette'
    return None


def build_special_export_basename(material_name, folder_kind):
    base_name = build_export_basename(material_name)
    if folder_kind == 'palette':
        return base_name + '_PaletteIndex'
    return base_name


def build_material_folder_name(material_name):
    if material_name.startswith('ALP_Mat_'):
        return sanitize_filename(material_name[len('ALP_Mat_'):])
    return sanitize_filename(material_name)


def build_material_project_path(material_name):
    folder_name = build_material_folder_name(material_name)
    root_dir = get_project_output_root_directory()
    target_dir = os.path.join(root_dir, folder_name)
    os.makedirs(target_dir, exist_ok=True)
    return os.path.join(target_dir, folder_name + '.spp')


def ensure_project_saved_before_export(material_name=None):
    if material_name is None:
        stack = get_active_stack()
        if stack is None:
            raise RuntimeError('\u5f53\u524d\u6ca1\u6709\u53ef\u7528\u7684 Texture Set\u3002')
        material_name = get_material_name(stack)

    project_path = substance_painter.project.file_path()
    if project_path:
        if substance_painter.project.needs_saving():
            substance_painter.project.save()
            substance_painter.logging.info('\u5df2\u5728\u5bfc\u51fa\u524d\u81ea\u52a8\u4fdd\u5b58\u5f53\u524d SPP\u3002')
        return project_path

    target_path = build_material_project_path(material_name)
    substance_painter.project.save_as(target_path)
    substance_painter.logging.info('\u5bfc\u51fa\u524d\u81ea\u52a8\u4fdd\u5b58 SPP\uff1a' + target_path)
    return target_path


def get_export_directory():
    global last_export_dir
    project_path = substance_painter.project.file_path()
    if not project_path:
        raise RuntimeError('\u8bf7\u5148\u4fdd\u5b58\u5de5\u7a0b\uff0c\u624d\u80fd\u786e\u5b9a\u5bfc\u51fa\u8def\u5f84\u3002')
    if export_dir_line_edit is not None:
        custom_dir = export_dir_line_edit.text().strip()
        if custom_dir:
            os.makedirs(custom_dir, exist_ok=True)
            if custom_dir != last_export_dir:
                last_export_dir = custom_dir
                save_plugin_settings()
            return custom_dir
    export_dir = os.path.dirname(project_path)
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


def get_project_output_root_directory():
    global last_project_dir
    if project_dir_line_edit is not None:
        custom_dir = project_dir_line_edit.text().strip()
        if custom_dir:
            os.makedirs(custom_dir, exist_ok=True)
            if custom_dir != last_project_dir:
                last_project_dir = custom_dir
                save_plugin_settings()
            return custom_dir

    project_path = substance_painter.project.file_path()
    if project_path:
        output_dir = os.path.dirname(project_path)
        os.makedirs(output_dir, exist_ok=True)
        return output_dir

    raise RuntimeError('\u8bf7\u5148\u6307\u5b9a\u5de5\u7a0b\u8f93\u51fa\u76ee\u5f55\uff0c\u6216\u8005\u5148\u4fdd\u5b58\u5f53\u524d .spp \u5de5\u7a0b\u3002')


def browse_export_directory():
    global last_export_dir
    if export_dir_line_edit is None:
        return
    current_dir = export_dir_line_edit.text().strip()
    if not current_dir:
        if last_export_dir:
            current_dir = last_export_dir
        else:
            project_path = substance_painter.project.file_path()
            if project_path:
                current_dir = os.path.dirname(project_path)
    selected_dir = QtWidgets.QFileDialog.getExistingDirectory(
        None, '\u9009\u62e9\u5bfc\u51fa\u76ee\u5f55', current_dir
    )
    if selected_dir:
        export_dir_line_edit.setText(selected_dir)
        last_export_dir = selected_dir
        save_plugin_settings()


def browse_project_directory():
    global last_project_dir
    if project_dir_line_edit is None:
        return
    current_dir = project_dir_line_edit.text().strip()
    if not current_dir:
        if last_project_dir:
            current_dir = last_project_dir
        else:
            project_path = substance_painter.project.file_path()
            if project_path:
                current_dir = os.path.dirname(project_path)
    selected_dir = QtWidgets.QFileDialog.getExistingDirectory(
        None, '\u9009\u62e9\u5de5\u7a0b\u8f93\u51fa\u76ee\u5f55', current_dir
    )
    if selected_dir:
        project_dir_line_edit.setText(selected_dir)
        last_project_dir = selected_dir
        save_plugin_settings()


def persist_export_directory():
    global last_export_dir
    if export_dir_line_edit is None:
        return
    last_export_dir = export_dir_line_edit.text().strip()
    save_plugin_settings()


def persist_project_directory():
    global last_project_dir
    if project_dir_line_edit is None:
        return
    last_project_dir = project_dir_line_edit.text().strip()
    save_plugin_settings()


def build_basecolor_export_config(stack, export_basename, export_dir):
    root_path = str(stack)
    preset_name = 'alp_basecolor_only'
    return {
        'exportShaderParams': False,
        'exportPath': export_dir,
        'defaultExportPreset': preset_name,
        'exportPresets': [{
            'name': preset_name,
            'maps': [{
                'fileName': export_basename,
                'channels': [
                    {'destChannel': 'R', 'srcChannel': 'R', 'srcMapType': 'documentMap', 'srcMapName': 'basecolor'},
                    {'destChannel': 'G', 'srcChannel': 'G', 'srcMapType': 'documentMap', 'srcMapName': 'basecolor'},
                    {'destChannel': 'B', 'srcChannel': 'B', 'srcMapType': 'documentMap', 'srcMapName': 'basecolor'},
                ],
                'parameters': {'fileFormat': 'png', 'bitDepth': '8', 'dithering': True, 'paddingAlgorithm': 'infinite'},
            }],
        }],
        'exportList': [{'rootPath': root_path}],
    }


def copy_exported_files_to_project_folder(exported_files):
    project_path = substance_painter.project.file_path()
    if not project_path:
        return []

    target_dir = os.path.dirname(project_path)
    copied_files = []
    for source_path in exported_files:
        if not source_path:
            continue
        source_dir = os.path.dirname(source_path)
        if os.path.normcase(os.path.normpath(source_dir)) == os.path.normcase(os.path.normpath(target_dir)):
            continue
        if not os.path.exists(source_path):
            continue
        target_path = os.path.join(target_dir, os.path.basename(source_path))
        shutil.copy2(source_path, target_path)
        copied_files.append(target_path)
    return copied_files


def export_basecolor_with_name(stack, export_basename, mirror_to_project_folder=False):
    export_dir = get_export_directory()
    export_basename = sanitize_filename(export_basename)
    config = build_basecolor_export_config(stack, export_basename, export_dir)
    result = substance_painter.export.export_project_textures(config)
    exported_files = []
    textures = getattr(result, 'textures', None)
    if textures is None and isinstance(result, dict):
        textures = result.get('textures', {})
    if textures is None:
        textures = {}
    for paths in textures.values():
        exported_files.extend(paths)
    if not exported_files:
        raise RuntimeError('\u5bfc\u51fa\u5b8c\u6210\uff0c\u4f46\u6ca1\u6709\u751f\u6210\u4efb\u4f55\u6587\u4ef6\u3002')
    substance_painter.logging.info('\u5df2\u5bfc\u51fa\uff1a' + exported_files[0])
    if mirror_to_project_folder:
        copied_files = copy_exported_files_to_project_folder(exported_files)
        if copied_files:
            substance_painter.logging.info('\u5df2\u540c\u6b65\u590d\u5236\u5230 SPP \u6587\u4ef6\u5939\uff1a' + copied_files[0])
    return exported_files


def export_current_basecolor():
    stack = get_active_stack()
    if stack is None:
        return
    material_name = get_material_name(stack)
    export_basename = build_export_basename(material_name)
    try:
        ensure_project_saved_before_export(material_name)
        export_basecolor_with_name(stack, export_basename)
    except Exception as exc:
        substance_painter.logging.warning('\u5bfc\u51fa\u5f53\u524d BaseColor \u5931\u8d25\uff1a' + str(exc))


def export_special_maps():
    data = get_active_stack_top_groups()
    if data is None:
        return
    stack, groups = data
    if not groups:
        substance_painter.logging.warning('\u5f53\u524d Texture Set \u4e0b\u6ca1\u6709\u627e\u5230\u9876\u5c42\u6587\u4ef6\u5939\u3002')
        return

    material_name = get_material_name(stack)
    try:
        ensure_project_saved_before_export(material_name)
    except Exception as exc:
        substance_painter.logging.warning('\u5bfc\u51fa\u524d\u81ea\u52a8\u4fdd\u5b58\u5931\u8d25\uff1a' + str(exc))
        return
    all_nodes = []
    original_visibility = {}
    for group in groups:
        node = substance_painter.layerstack.Node(group['uid'])
        all_nodes.append((group['name'], node))
        original_visibility[get_node_uid(node)] = node.is_visible()

    matched_groups = []
    for group in groups:
        folder_kind = classify_export_folder(group['name'])
        if folder_kind is None:
            continue
        matched_groups.append({
            'kind': folder_kind,
            'name': group['name'],
            'node': substance_painter.layerstack.Node(group['uid']),
        })

    if not matched_groups:
        substance_painter.logging.warning(
            '\u6ca1\u6709\u627e\u5230\u53ef\u5bfc\u51fa\u7684\u76ee\u6807\u6587\u4ef6\u5939\uff0c'
            '\u8bf7\u521b\u5efa\u540d\u4e3a\u201c\u5149\u7167\u4fe1\u606f\u201d\u548c/\u6216\u201cID\u901a\u9053\u201d\u7684\u9876\u5c42\u6587\u4ef6\u5939\u3002'
        )
        return

    exported_kinds = set()
    try:
        for group in matched_groups:
            target_uid = get_node_uid(group['node'])
            for _, node in all_nodes:
                node.set_visible(get_node_uid(node) == target_uid)
            QtWidgets.QApplication.processEvents()
            export_name = build_special_export_basename(material_name, group['kind'])
            export_basecolor_with_name(stack, export_name, mirror_to_project_folder=True)
            exported_kinds.add(group['kind'])
    except Exception as exc:
        substance_painter.logging.warning('\u6309\u6587\u4ef6\u5939\u5bfc\u51fa\u5931\u8d25\uff1a' + str(exc))
    finally:
        for _, node in all_nodes:
            node.set_visible(original_visibility.get(get_node_uid(node), True))
        QtWidgets.QApplication.processEvents()

    missing_kinds = []
    if 'lighting' not in exported_kinds:
        missing_kinds.append('\u5149\u7167\u4fe1\u606f')
    if 'palette' not in exported_kinds:
        missing_kinds.append('ID\u901a\u9053')

    if missing_kinds:
        substance_painter.logging.warning(
            '\u7f3a\u5c11\u4ee5\u4e0b\u9876\u5c42\u6587\u4ef6\u5939\uff1a' + '\u3001'.join(missing_kinds)
        )
    elif exported_kinds:
        substance_painter.logging.info(
            '\u5df2\u6309 ALP \u89c4\u5219\u5bfc\u51fa\u5149\u7167\u4fe1\u606f\u56fe\u548c ID \u56fe\u3002'
        )


def export_single_special_map(target_kind):
    data = get_active_stack_top_groups()
    if data is None:
        return
    stack, groups = data
    if not groups:
        substance_painter.logging.warning('\u5f53\u524d Texture Set \u4e0b\u6ca1\u6709\u627e\u5230\u9876\u5c42\u6587\u4ef6\u5939\u3002')
        return

    material_name = get_material_name(stack)
    try:
        ensure_project_saved_before_export(material_name)
    except Exception as exc:
        substance_painter.logging.warning('\u5bfc\u51fa\u524d\u81ea\u52a8\u4fdd\u5b58\u5931\u8d25\uff1a' + str(exc))
        return
    all_nodes = []
    original_visibility = {}
    target_node = None
    target_label = '\u5149\u7167\u4fe1\u606f' if target_kind == 'lighting' else 'ID\u901a\u9053'
    for group in groups:
        node = substance_painter.layerstack.Node(group['uid'])
        all_nodes.append((group['name'], node))
        original_visibility[get_node_uid(node)] = node.is_visible()
        if classify_export_folder(group['name']) == target_kind:
            target_node = node

    if target_node is None:
        substance_painter.logging.warning(
            '\u6ca1\u6709\u627e\u5230\u9876\u5c42\u6587\u4ef6\u5939\u201c{0}\u201d\u3002'.format(target_label)
        )
        return

    try:
        target_uid = get_node_uid(target_node)
        for _, node in all_nodes:
            node.set_visible(get_node_uid(node) == target_uid)
        QtWidgets.QApplication.processEvents()
        export_name = build_special_export_basename(material_name, target_kind)
        export_basecolor_with_name(stack, export_name, mirror_to_project_folder=True)
        substance_painter.logging.info(
            '\u5df2\u5bfc\u51fa{0}\uff1a{1}'.format(target_label, export_name)
        )
    except Exception as exc:
        substance_painter.logging.warning(
            '\u5bfc\u51fa{0}\u5931\u8d25\uff1a'.format(target_label) + str(exc)
        )
    finally:
        for _, node in all_nodes:
            node.set_visible(original_visibility.get(get_node_uid(node), True))
        QtWidgets.QApplication.processEvents()


def export_lighting_map():
    export_single_special_map('lighting')


def export_palette_index_map():
    export_single_special_map('palette')


def save_project_to_material_folder():
    stack = get_active_stack()
    if stack is None:
        return

    material_name = get_material_name(stack)

    try:
        target_path = build_material_project_path(material_name)
        substance_painter.project.save_as(target_path)
        substance_painter.logging.info(
            '\u5df2\u521b\u5efa\u6587\u4ef6\u5939\u5e76\u4fdd\u5b58 SPP\uff1a' + target_path
        )
    except Exception as exc:
        substance_painter.logging.warning(
            '\u521b\u5efa\u6587\u4ef6\u5939\u5e76\u4fdd\u5b58 SPP \u5931\u8d25\uff1a' + str(exc)
        )


def get_active_stack_top_groups():
    stack = get_active_stack()
    if stack is None:
        return None, []
    root_path = str(stack)
    doc = substance_painter.js.evaluate('alg.mapexport.documentStructure()')
    groups = []
    for material in doc.get('materials', []):
        material_name = material.get('name', '')
        stacks = material.get('stacks', [])
        if not stacks:
            full_path = material_name
            if full_path != root_path:
                continue
            root_layers = material.get('layers', [])
        else:
            matched_stack = None
            for js_stack in stacks:
                stack_name = js_stack.get('name', '')
                full_path = material_name if not stack_name else material_name + '/' + stack_name
                if full_path == root_path:
                    matched_stack = js_stack
                    break
            if matched_stack is None:
                continue
            root_layers = matched_stack.get('layers', [])
        for layer in root_layers:
            if 'layers' not in layer:
                continue
            uid = layer.get('uid')
            name = layer.get('name')
            if uid is None or not name:
                continue
            groups.append({'uid': uid, 'name': name})
        break
    return stack, groups


def export_basecolor_by_top_groups():
    data = get_active_stack_top_groups()
    if data is None:
        return
    stack, groups = data
    if not groups:
        substance_painter.logging.warning('No top-level folders found in the current Texture Set.')
        return
    material_name = get_material_name(stack)
    base_name = build_export_basename(material_name)
    nodes = []
    original_visibility = {}
    for group in groups:
        node = substance_painter.layerstack.Node(group['uid'])
        nodes.append((group['name'], node))
        original_visibility[group['uid']] = node.is_visible()
    try:
        for target_name, target_node in nodes:
            target_uid = get_node_uid(target_node)
            for _, node in nodes:
                node.set_visible(get_node_uid(node) == target_uid)
            QtWidgets.QApplication.processEvents()
            export_name = '{0}_{1}'.format(base_name, sanitize_filename(target_name))
            export_basecolor_with_name(stack, export_name)
    except Exception as exc:
        substance_painter.logging.warning('Failed to export by top folder: ' + str(exc))
    finally:
        for _, node in nodes:
            node.set_visible(original_visibility.get(get_node_uid(node), True))
        QtWidgets.QApplication.processEvents()


class RecolorToolWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName('alp_recolor_export_panel')
        self.setWindowTitle('ALP \u91cd\u7740\u8272\u5bfc\u51fa')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title = QtWidgets.QLabel('\u5f53\u524d\u989c\u8272')
        title.setStyleSheet('font-weight: 600;')
        layout.addWidget(title)

        preview_layout = QtWidgets.QHBoxLayout()
        preview_layout.setSpacing(6)
        global color_swatch_label
        color_swatch_label = QtWidgets.QLabel()
        color_swatch_label.setFixedSize(44, 44)
        preview_layout.addWidget(color_swatch_label)

        picker_button = QtWidgets.QPushButton('Qt \u5438\u8272')
        picker_button.clicked.connect(open_color_dialog)
        picker_button.setToolTip(
            '\u6253\u5f00 Qt \u53d6\u8272\u9762\u677f\uff0c\u5e76\u76f4\u63a5\u5c1d\u8bd5\u8fdb\u5165\u5c4f\u5e55\u5438\u8272\u6a21\u5f0f\u3002'
        )
        preview_layout.addWidget(picker_button)
        preview_layout.addStretch(1)
        layout.addLayout(preview_layout)

        global color_hex_label
        color_hex_label = QtWidgets.QLabel()
        color_hex_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(color_hex_label)

        manual_color_layout = QtWidgets.QHBoxLayout()
        global manual_color_line_edit
        manual_color_line_edit = QtWidgets.QLineEdit()
        manual_color_line_edit.setPlaceholderText('#FF6600')
        manual_color_layout.addWidget(manual_color_line_edit, 1)

        set_manual_color_button = QtWidgets.QPushButton('\u8bbe\u7f6e\u5341\u516d\u8fdb\u5236\u989c\u8272')
        set_manual_color_button.clicked.connect(set_current_color_from_text)
        manual_color_layout.addWidget(set_manual_color_button)
        layout.addLayout(manual_color_layout)

        read_color_button = QtWidgets.QPushButton('\u8bfb\u53d6\u9009\u4e2d\u586b\u5145\u5c42\u989c\u8272')
        read_color_button.clicked.connect(load_selected_fill_layer_color)
        layout.addWidget(read_color_button)

        apply_color_button = QtWidgets.QPushButton('\u5f53\u524d\u989c\u8272\u586b\u5145\u5230\u9009\u4e2d\u586b\u5145\u5c42')
        apply_color_button.clicked.connect(apply_current_color_to_selected_fill_layers)
        layout.addWidget(apply_color_button)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(line)

        export_dir_title = QtWidgets.QLabel('\u5bfc\u51fa\u76ee\u5f55')
        export_dir_title.setStyleSheet('font-weight: 600;')
        layout.addWidget(export_dir_title)

        export_dir_layout = QtWidgets.QHBoxLayout()
        global export_dir_line_edit
        export_dir_line_edit = QtWidgets.QLineEdit()
        export_dir_line_edit.setPlaceholderText('\u7559\u7a7a\u5219\u5bfc\u51fa\u5230 .spp \u540c\u76ee\u5f55')
        export_dir_line_edit.setText(last_export_dir)
        export_dir_line_edit.editingFinished.connect(persist_export_directory)
        export_dir_layout.addWidget(export_dir_line_edit, 1)

        browse_button = QtWidgets.QPushButton('\u6d4f\u89c8')
        browse_button.clicked.connect(browse_export_directory)
        export_dir_layout.addWidget(browse_button)
        layout.addLayout(export_dir_layout)

        project_dir_title = QtWidgets.QLabel('\u5de5\u7a0b\u8f93\u51fa\u76ee\u5f55')
        project_dir_title.setStyleSheet('font-weight: 600;')
        layout.addWidget(project_dir_title)

        project_dir_layout = QtWidgets.QHBoxLayout()
        global project_dir_line_edit
        project_dir_line_edit = QtWidgets.QLineEdit()
        project_dir_line_edit.setPlaceholderText(
            '\u7559\u7a7a\u5219\u4f7f\u7528\u5f53\u524d .spp \u6240\u5728\u76ee\u5f55'
        )
        project_dir_line_edit.setText(last_project_dir)
        project_dir_line_edit.editingFinished.connect(persist_project_directory)
        project_dir_layout.addWidget(project_dir_line_edit, 1)

        project_browse_button = QtWidgets.QPushButton('\u6d4f\u89c8')
        project_browse_button.clicked.connect(browse_project_directory)
        project_dir_layout.addWidget(project_browse_button)
        layout.addLayout(project_dir_layout)

        save_project_button = QtWidgets.QPushButton(
            '\u6309\u6750\u8d28\u540d\u5efa\u6587\u4ef6\u5939\u5e76\u4fdd\u5b58 SPP'
        )
        save_project_button.clicked.connect(save_project_to_material_folder)
        layout.addWidget(save_project_button)

        export_button = QtWidgets.QPushButton('\u5bfc\u51fa\u5f53\u524d\u53ef\u89c1 BaseColor')
        export_button.clicked.connect(export_current_basecolor)
        layout.addWidget(export_button)

        export_lighting_button = QtWidgets.QPushButton('\u5bfc\u51fa\u5149\u7167\u4fe1\u606f')
        export_lighting_button.clicked.connect(export_lighting_map)
        layout.addWidget(export_lighting_button)

        export_palette_button = QtWidgets.QPushButton('\u5bfc\u51fa ID \u56fe')
        export_palette_button.clicked.connect(export_palette_index_map)
        layout.addWidget(export_palette_button)

        hint = QtWidgets.QLabel(
            '\u5bfc\u51fa\u76ee\u5f55\u7559\u7a7a\u65f6\uff0c\u9ed8\u8ba4\u4f7f\u7528 .spp \u6240\u5728\u76ee\u5f55\n'
            '\u5de5\u7a0b\u4fdd\u5b58\u6309\u94ae\u4f1a\u521b\u5efa\u201c\u7c7b\u578b_\u540d\u79f0\u201d\u6587\u4ef6\u5939\uff0c\u5e76\u4fdd\u5b58\u4e3a\u540c\u540d .spp\n'
            '\u5f53\u5de5\u7a0b\u672a\u4fdd\u5b58\u65f6\uff0c\u5bfc\u51fa\u524d\u4f1a\u5148\u81ea\u52a8\u4fdd\u5b58 SPP\n'
            '\u6750\u8d28\u547d\u540d\uff1aALP_Mat_\u7c7b\u578b_\u540d\u79f0 -> ALP_Tx_\u7c7b\u578b_\u540d\u79f0\n'
            '\u5bfc\u51fa ID \u56fe\u548c\u5149\u7167\u4fe1\u606f\u65f6\uff0c\u4f1a\u989d\u5916\u590d\u5236\u4e00\u4efd\u5230\u5f53\u524d SPP \u6240\u5728\u6587\u4ef6\u5939\n'
            '\u9876\u5c42\u6587\u4ef6\u5939\u201c\u5149\u7167\u4fe1\u606f\u201d\u5bfc\u51fa\u4e3a ALP_Tx_\u7c7b\u578b_\u540d\u79f0\n'
            '\u9876\u5c42\u6587\u4ef6\u5939\u201cID\u901a\u9053\u201d\u5bfc\u51fa\u4e3a ALP_Tx_\u7c7b\u578b_\u540d\u79f0_PaletteIndex'
        )
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #BBBBBB;')
        layout.addWidget(hint)
        layout.addStretch(1)
        update_color_preview()


def open_panel():
    global tool_widget
    global tool_dock
    if tool_widget is None:
        tool_widget = RecolorToolWidget()
        tool_dock = substance_painter.ui.add_dock_widget(tool_widget)
        plugin_widgets.append(tool_dock)
    else:
        tool_dock.show()
        tool_dock.raise_()


def start_plugin():
    load_plugin_settings()
    panel_action = QtGui.QAction('\u6253\u5f00 ALP \u91cd\u7740\u8272\u5bfc\u51fa\u9762\u677f', None)
    panel_action.triggered.connect(open_panel)
    substance_painter.ui.add_action(substance_painter.ui.ApplicationMenu.File, panel_action)
    plugin_widgets.append(panel_action)
    open_panel()


def close_plugin():
    global tool_widget
    global tool_dock
    global color_hex_label
    global color_swatch_label
    global export_dir_line_edit
    global project_dir_line_edit
    global manual_color_line_edit
    global quick_color_buttons

    for widget in plugin_widgets:
        substance_painter.ui.delete_ui_element(widget)
    plugin_widgets.clear()
    tool_widget = None
    tool_dock = None
    color_hex_label = None
    color_swatch_label = None
    export_dir_line_edit = None
    project_dir_line_edit = None
    manual_color_line_edit = None
    quick_color_buttons = []
