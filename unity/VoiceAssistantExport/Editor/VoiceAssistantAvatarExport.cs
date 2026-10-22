// Voice Assistant: avatar export (Unity 2022, VRChat Avatars SDK 3).
// Reads the selected avatar's expression menu, expression parameters and FX layer straight from Unity (so it also works when a tool such as VRCFury keeps
// them inside a generated asset file) and writes one small JSON file the Voice Assistant imports: %APPDATA%\VoiceAssistant\unity-exports\<avatar>.json
//
// It only READS the avatar. It changes nothing in your project and sends nothing anywhere.
//
// Use: select the avatar in the Hierarchy, then Tools > Voice Assistant > Export selected avatar.
// For avatars built by VRCFury (or similar) run it in Play mode with the Avatars 3.0 Emulator, or after "Build & Test", so the generated menu and FX exist.
// Then wear the avatar in VRChat and tell the assistant: "import this avatar".
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;

namespace VoiceAssistant
{
    public static class VoiceAssistantAvatarExport
    {
        class Entry
        {
            public string Label, Kind;
            public List<string> Params = new List<string>();
            public Dictionary<string, float> Values = new Dictionary<string, float>();
        }

        class Switch
        {
            public bool Inverted;
            public string Why;
        }

        [MenuItem("Tools/Voice Assistant/Export selected avatar")]
        static void ExportSelected()
        {
            var desc = FindDescriptor();
            if (desc == null)
            {
                EditorUtility.DisplayDialog("Voice Assistant", "Select an avatar (a GameObject with a VRC Avatar Descriptor) in the Hierarchy first.", "OK");
                return;
            }
            try
            {
                var report = Export(desc, out var path);
                EditorUtility.DisplayDialog("Voice Assistant", report + "\n\nSaved to:\n" + path + "\n\nNow wear the avatar in VRChat and tell the assistant: import this avatar.", "OK");
                EditorUtility.RevealInFinder(path);
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                EditorUtility.DisplayDialog("Voice Assistant", "Export failed: " + e.Message, "OK");
            }
        }

        [MenuItem("Tools/Voice Assistant/Export selected avatar", true)]
        static bool ExportSelectedValidate() { return FindDescriptor() != null; }

        static VRCAvatarDescriptor FindDescriptor()
        {
            var go = Selection.activeGameObject;
            if (go != null)
            {
                var d = go.GetComponentInParent<VRCAvatarDescriptor>();
                if (d != null) return d;
            }
            var all = UnityEngine.Object.FindObjectsOfType<VRCAvatarDescriptor>();
            return all.Length == 1 ? all[0] : null;
        }

        public static string Export(VRCAvatarDescriptor desc, out string path)
        {
            var name = desc.gameObject.name.Replace("(Clone)", "").Trim();
            var id = BlueprintId(desc.gameObject);

            var entries = new List<Entry>();
            if (desc.expressionsMenu != null)
                Flatten(desc.expressionsMenu, "", entries, new HashSet<VRCExpressionsMenu>(), 0);

            var parameters = new List<string[]>();
            if (desc.expressionParameters != null && desc.expressionParameters.parameters != null)
                foreach (var p in desc.expressionParameters.parameters)
                    if (p != null && !string.IsNullOrEmpty(p.name))
                        parameters.Add(new[] { p.name, p.valueType.ToString(), p.defaultValue.ToString(System.Globalization.CultureInfo.InvariantCulture) });

            var switches = new Dictionary<string, Switch>();
            string fxName = "";
            var fx = FindFx(desc, out fxName);
            if (fx != null) AnalyzeFx(fx, switches);

            var sb = new StringBuilder();
            sb.Append("{\n \"format\": \"voice-assistant-avatar\",\n \"version\": 1,\n");
            sb.Append(" \"avatar\": {\"name\": ").Append(Q(name)).Append(", \"id\": ").Append(Q(id)).Append("},\n");
            sb.Append(" \"unity\": {\"project\": ").Append(Q(Path.GetFileName(Path.GetDirectoryName(Application.dataPath) ?? ""))).Append(", \"fx\": ").Append(Q(fxName)).Append("},\n");
            sb.Append(" \"parameters\": [");
            sb.Append(string.Join(",", parameters.Select(p => "\n  {\"name\": " + Q(p[0]) + ", \"type\": " + Q(p[1]) + ", \"default\": " + Q(p[2]) + "}")));
            sb.Append("\n ],\n \"menu\": [");
            sb.Append(string.Join(",", entries.Select(e =>
                "\n  {\"label\": " + Q(e.Label) + ", \"kind\": " + Q(e.Kind) + ", \"params\": [" + string.Join(", ", e.Params.Select(Q)) + "], \"values\": {"
                + string.Join(", ", e.Values.Select(kv => Q(kv.Key) + ": " + kv.Value.ToString(System.Globalization.CultureInfo.InvariantCulture))) + "}}")));
            sb.Append("\n ],\n \"switches\": {");
            sb.Append(string.Join(",", switches.Select(kv => "\n  " + Q(kv.Key) + ": {\"inverted\": " + (kv.Value.Inverted ? "true" : "false") + ", \"why\": " + Q(kv.Value.Why) + "}")));
            sb.Append("\n }\n}\n");

            var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "VoiceAssistant", "unity-exports");
            Directory.CreateDirectory(dir);
            var safe = new string(name.Select(c => Path.GetInvalidFileNameChars().Contains(c) ? '_' : c).ToArray());
            path = Path.Combine(dir, (string.IsNullOrEmpty(safe) ? "avatar" : safe) + ".json");
            File.WriteAllText(path, sb.ToString(), new UTF8Encoding(false));

            var backwards = switches.Count(kv => kv.Value.Inverted);
            return $"Avatar: {name}" + (id == "" ? " (no blueprint id yet: upload it once, or tell the assistant which avatar this is)" : " (" + id + ")")
                   + $"\nMenu entries: {entries.Count}\nParameters: {parameters.Count}"
                   + (fx == null ? "\nNo FX layer found, so backwards switches are unknown." : $"\nFX layer '{fxName}': {switches.Count} switches checked, {backwards} work backwards.");
        }

        // The avatar's id as shown under "Blueprint ID" in the Pipeline Manager (read by name, so it works across SDK versions). Empty until the avatar has been uploaded once.
        static string BlueprintId(GameObject go)
        {
            foreach (var c in go.GetComponents<Component>())
            {
                if (c == null || c.GetType().Name != "PipelineManager") continue;
                var field = c.GetType().GetField("blueprintId");
                var value = field != null ? field.GetValue(c) as string : null;
                return value ?? "";
            }
            return "";
        }

        // ---- menu -------------------------------------------------------------------------------

        static void Flatten(VRCExpressionsMenu menu, string prefix, List<Entry> output, HashSet<VRCExpressionsMenu> seen, int depth)
        {
            if (menu == null || depth > 8 || !seen.Add(menu)) return;
            foreach (var c in menu.controls)
            {
                var label = prefix + (c.name ?? "");
                var pname = c.parameter != null ? c.parameter.name : "";
                switch (c.type)
                {
                    case VRCExpressionsMenu.Control.ControlType.SubMenu:
                    {
                        if (c.subMenu == null) { output.Add(new Entry { Label = label, Kind = "folder" }); break; }
                        var inner = new List<Entry>();
                        Flatten(c.subMenu, label + "/", inner, seen, depth + 1);
                        var folder = new Entry { Label = label, Kind = "folder" };
                        var seenValues = new Dictionary<string, HashSet<float>>();
                        foreach (var e in inner.Where(e => e.Kind == "toggle" || e.Kind == "button"))
                            foreach (var p in e.Params)
                            {
                                if (!seenValues.ContainsKey(p)) { seenValues[p] = new HashSet<float>(); folder.Params.Add(p); }
                                float v; if (e.Values.TryGetValue(p, out v)) seenValues[p].Add(v);
                            }
                        // a parameter several items pick a number for (hair colour 0-4) is a choice, not something to switch with the folder
                        folder.Params.RemoveAll(p => seenValues[p].Count > 1);
                        foreach (var e in inner.Where(e => e.Kind == "toggle" || e.Kind == "button"))
                            foreach (var p in e.Params.Where(folder.Params.Contains))
                                if (e.Values.ContainsKey(p)) folder.Values[p] = e.Values[p];
                        output.Add(folder);
                        output.AddRange(inner);
                        seen.Remove(c.subMenu);
                        break;
                    }
                    case VRCExpressionsMenu.Control.ControlType.Toggle:
                    case VRCExpressionsMenu.Control.ControlType.Button:
                        if (!string.IsNullOrEmpty(pname))
                        {
                            var e = new Entry { Label = label, Kind = c.type == VRCExpressionsMenu.Control.ControlType.Toggle ? "toggle" : "button" };
                            e.Params.Add(pname);
                            e.Values[pname] = c.value;
                            output.Add(e);
                        }
                        break;
                    case VRCExpressionsMenu.Control.ControlType.RadialPuppet:
                        if (!string.IsNullOrEmpty(pname))
                        {
                            var e = new Entry { Label = label, Kind = "radial" };
                            e.Params.Add(pname);
                            output.Add(e);
                        }
                        break;
                }
            }
        }

        // ---- FX layer: which switches work backwards ---------------------------------------------

        static AnimatorController FindFx(VRCAvatarDescriptor desc, out string name)
        {
            name = "";
            foreach (var layer in desc.baseAnimationLayers)
            {
                if (layer.type != VRCAvatarDescriptor.AnimLayerType.FX) continue;
                RuntimeAnimatorController rc = layer.animatorController;
                var over = rc as AnimatorOverrideController;
                if (over != null) rc = over.runtimeAnimatorController;
                var ac = rc as AnimatorController;
                if (ac != null) name = ac.name;
                return ac;
            }
            return null;
        }

        static IEnumerable<AnimatorTransitionBase> Transitions(AnimatorStateMachine sm, int depth = 0)
        {
            if (sm == null || depth > 12) yield break;
            foreach (var t in sm.anyStateTransitions) yield return t;
            foreach (var t in sm.entryTransitions) yield return t;
            foreach (var cs in sm.states)
                foreach (var t in cs.state.transitions) yield return t;
            foreach (var child in sm.stateMachines)
                foreach (var t in Transitions(child.stateMachine, depth + 1)) yield return t;
        }

        static IEnumerable<AnimationClip> Clips(Motion m, int depth = 0)
        {
            if (m == null || depth > 8) yield break;
            var clip = m as AnimationClip;
            if (clip != null) { yield return clip; yield break; }
            var tree = m as BlendTree;
            if (tree != null)
                foreach (var ch in tree.children)
                    foreach (var c in Clips(ch.motion, depth + 1)) yield return c;
        }

        // What a clip does to the item: GameObject on/off (m_IsActive) and blend shapes named like "Hide Socks" (100 = hidden) or "Show Wings" (100 = shown).
        // Placeholder objects (names starting with an underscore) and every other animation are ignored.
        static Dictionary<string, bool> Shown(AnimationClip clip)
        {
            var result = new Dictionary<string, bool>();
            foreach (var b in AnimationUtility.GetCurveBindings(clip))
            {
                var last = b.path.Contains("/") ? b.path.Substring(b.path.LastIndexOf('/') + 1) : b.path;
                if (last.StartsWith("_")) continue;
                var curve = AnimationUtility.GetEditorCurve(clip, b);
                if (curve == null || curve.length == 0) continue;
                var value = curve[curve.length - 1].value;
                if (b.propertyName == "m_IsActive") result[b.path] = value >= 0.5f;
                else if (b.propertyName.StartsWith("blendShape."))
                {
                    var shape = b.propertyName.Substring("blendShape.".Length).Trim();
                    var lower = shape.ToLowerInvariant();
                    if (lower.StartsWith("hide") || lower.StartsWith("remove") || lower.StartsWith("disable")) result[b.path + ": " + shape] = value < 50f;
                    else if (lower.StartsWith("show") || lower.StartsWith("enable") || lower.StartsWith("visible")) result[b.path + ": " + shape] = value >= 50f;
                }
            }
            return result;
        }

        static void AnalyzeFx(AnimatorController fx, Dictionary<string, Switch> output)
        {
            var bools = new HashSet<string>(fx.parameters.Where(p => p.type == AnimatorControllerParameterType.Bool).Select(p => p.name));
            var on = new Dictionary<string, Dictionary<string, bool>>();
            var off = new Dictionary<string, Dictionary<string, bool>>();
            foreach (var layer in fx.layers)
                foreach (var t in Transitions(layer.stateMachine))
                {
                    if (t.destinationState == null) continue;
                    foreach (var cond in t.conditions)
                    {
                        if (!bools.Contains(cond.parameter)) continue;
                        if (cond.mode != AnimatorConditionMode.If && cond.mode != AnimatorConditionMode.IfNot) continue;
                        var side = cond.mode == AnimatorConditionMode.If ? on : off;
                        if (!side.ContainsKey(cond.parameter)) side[cond.parameter] = new Dictionary<string, bool>();
                        foreach (var clip in Clips(t.destinationState.motion))
                            foreach (var kv in Shown(clip))
                            {
                                var d = side[cond.parameter];
                                d[kv.Key] = d.ContainsKey(kv.Key) ? d[kv.Key] && kv.Value : kv.Value; // different layers agreeing: all must show it to count as shown
                            }
                    }
                }
            foreach (var name in on.Keys.Union(off.Keys))
            {
                Dictionary<string, bool> a, b;
                on.TryGetValue(name, out a); off.TryGetValue(name, out b);
                bool? inverted = null; Dictionary<string, bool> evidence = null; string how = "";
                if (a != null && a.Count > 0)
                {
                    evidence = a;
                    if (a.Values.All(v => !v)) { inverted = true; how = "ON hides "; }
                    else if (a.Values.All(v => v)) { inverted = false; how = "ON shows "; }
                }
                else if (b != null && b.Count > 0)
                {
                    evidence = b;
                    if (b.Values.All(v => v)) { inverted = true; how = "OFF shows "; }
                    else if (b.Values.All(v => !v)) { inverted = false; how = "OFF hides "; }
                }
                if (inverted == null) continue;
                var objs = string.Join(", ", evidence.Keys.Select(k => k.Contains(": ") ? k.Substring(k.IndexOf(": ", StringComparison.Ordinal) + 2) : k.Substring(k.LastIndexOf('/') + 1)).Distinct().OrderBy(s => s).Take(3));
                output[name] = new Switch { Inverted = inverted.Value, Why = how + objs + " (read from " + fx.name + ")" };
            }
        }

        static string Q(string s)
        {
            if (s == null) return "\"\"";
            var sb = new StringBuilder("\"");
            foreach (var c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4")); else sb.Append(c);
                        break;
                }
            }
            return sb.Append('"').ToString();
        }
    }
}
