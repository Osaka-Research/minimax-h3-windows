"""
Flattens a ComfyUI UI-format workflow (nodes/links, possibly containing
subgraph instances) into the flat API-format graph {node_id: {class_type,
inputs}} that ComfyUI's /prompt endpoint requires.

This reimplements what ComfyUI's frontend does when you click "Export (API
Format)", server-side, using ComfyUI's own /object_info (each node class's
declared input order and types) so no browser is needed. Verified against
the real official MiniMax H3 text-to-video template
(Comfy-Org/workflow_templates: templates/video_minimax_h3_t2v.json), which
uses exactly the subgraph-instance shape handled here.
"""

PRIMITIVE_WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

# UI-only node types with no backend/object_info entry - ComfyUI's own API
# export skips these too, so we must never try to emit them.
NON_EXECUTING_NODE_TYPES = {"MarkdownNote", "Note"}


def is_widget_type(type_field) -> bool:
    """A type is widget-eligible (gets a slot in widgets_values) only if it's an
    exact scalar/combo type. Compound types (e.g. "FLOAT,INT,BOOLEAN", used by
    nodes that force a socket for otherwise-primitive types) intentionally do
    NOT match here - real workflow data confirms these consume no widgets_values
    slot."""
    if isinstance(type_field, list):
        return True
    return type_field in PRIMITIVE_WIDGET_TYPES


class ObjectInfoSource:
    """Wraps a class_type -> object_info dict, as returned by GET /object_info."""

    def __init__(self, object_info: dict):
        self._info = object_info

    def ordered_inputs(self, class_type: str):
        info = self._info.get(class_type)
        if info is None:
            raise RuntimeError(
                f"No /object_info schema for node class '{class_type}' - "
                "is a required (custom) node missing from this ComfyUI install?"
            )
        inp = info["input"]
        required = list(inp.get("required", {}).items())
        optional = list(inp.get("optional", {}).items())
        return [(name, spec[0]) for name, spec in required] + [(name, spec[0]) for name, spec in optional]


def _normalize_link(link):
    """Top-level links are lists [id, origin_id, origin_slot, target_id, target_slot,
    type]; links inside a subgraph definition are dicts with the same fields."""
    if isinstance(link, dict):
        return link
    lid, origin_id, origin_slot, target_id, target_slot, ltype = link
    return {"id": lid, "origin_id": origin_id, "origin_slot": origin_slot,
            "target_id": target_id, "target_slot": target_slot, "type": ltype}


class _Scope:
    """One level of graph: the top-level workflow, or one subgraph definition's
    inner graph."""

    def __init__(self, nodes, links, path):
        self.nodes_by_id = {n["id"]: n for n in nodes}
        self.links_by_id = {}
        for link in links:
            nl = _normalize_link(link)
            self.links_by_id[nl["id"]] = nl
        self.path = path  # tuple identifying this scope, for memoization keys


class WorkflowFlattener:
    def __init__(self, workflow: dict, object_info: ObjectInfoSource):
        self.workflow = workflow
        self.object_info = object_info
        self.subgraph_defs = {sg["id"]: sg for sg in workflow.get("definitions", {}).get("subgraphs", [])}
        all_ids = [n["id"] for n in workflow["nodes"]]
        for sg in self.subgraph_defs.values():
            all_ids += [n["id"] for n in sg["nodes"]]
        self._next_id = max(all_ids, default=0) + 1
        self.flat = {}
        self._id_memo = {}  # (scope_path, original_node_id) -> flat_id

    def flatten(self) -> dict:
        top_scope = _Scope(self.workflow["nodes"], self.workflow["links"], path=())
        for n in self.workflow["nodes"]:
            if n["type"] in NON_EXECUTING_NODE_TYPES:
                continue
            if n["type"] not in self.subgraph_defs:
                self._emit(n["id"], top_scope, outer_ctx=None)
        return self.flat

    def _alloc_id(self):
        nid = self._next_id
        self._next_id += 1
        return nid

    def _emit(self, node_id, scope: _Scope, outer_ctx):
        """Ensure the real node at node_id (in this scope) has been written to
        self.flat. Returns its flat id (int)."""
        key = (scope.path, node_id)
        if key in self._id_memo:
            return self._id_memo[key]

        node = scope.nodes_by_id[node_id]
        flat_id = self._alloc_id()
        self._id_memo[key] = flat_id

        class_type = node["type"]
        ordered = self.object_info.ordered_inputs(class_type)
        widget_names = [name for name, typ in ordered if is_widget_type(typ)]
        widget_values = node.get("widgets_values") or []
        widget_value_by_name = dict(zip(widget_names, widget_values))

        node_inputs_by_name = {i["name"]: i for i in node.get("inputs", [])}
        inputs_out = {}
        for name, typ in ordered:
            sock = node_inputs_by_name.get(name)
            if sock is not None and sock.get("link") is not None:
                resolved = self._resolve_link(sock["link"], scope, outer_ctx)
                if resolved is not None:
                    inputs_out[name] = resolved
                elif name in widget_value_by_name:
                    inputs_out[name] = widget_value_by_name[name]
            elif name in widget_value_by_name:
                inputs_out[name] = widget_value_by_name[name]

        self.flat[str(flat_id)] = {"class_type": class_type, "inputs": inputs_out}
        return flat_id

    def _resolve_link(self, link_id, scope: _Scope, outer_ctx):
        link = scope.links_by_id[link_id]
        return self._resolve_output(link["origin_id"], link["origin_slot"], scope, outer_ctx)

    def _resolve_output(self, node_id, slot, scope: _Scope, outer_ctx):
        if node_id == -10:
            if outer_ctx is None:
                raise RuntimeError("Boundary input reference outside any subgraph - malformed workflow")
            return self._resolve_boundary_input(slot, outer_ctx)

        node = scope.nodes_by_id[node_id]
        if node["type"] in self.subgraph_defs:
            inner_sg = self.subgraph_defs[node["type"]]
            inner_scope = _Scope(inner_sg["nodes"], inner_sg["links"], path=scope.path + (node_id,))
            out_decl = inner_sg["outputs"][slot]
            inner_link_id = out_decl["linkIds"][0]
            new_ctx = {
                "instance_node": node, "instance_scope": scope, "outer_ctx": outer_ctx,
                "subgraph_def": inner_sg,
            }
            return self._resolve_link(inner_link_id, inner_scope, new_ctx)

        flat_id = self._emit(node_id, scope, outer_ctx)
        return [str(flat_id), slot]

    def _resolve_boundary_input(self, slot, ctx):
        sg_def = ctx["subgraph_def"]
        boundary_name = sg_def["inputs"][slot]["name"]
        instance_node = ctx["instance_node"]
        instance_scope = ctx["instance_scope"]
        outer_ctx = ctx["outer_ctx"]

        node_inputs_by_name = {i["name"]: i for i in instance_node.get("inputs", [])}
        sock = node_inputs_by_name.get(boundary_name)
        if sock is not None and sock.get("link") is not None:
            return self._resolve_link(sock["link"], instance_scope, outer_ctx)

        # No link - either not exposed as a socket on the instance at all, or
        # exposed but left at its default (a widget-backed boundary socket
        # with link=null still means "use widgets_values", same as any
        # ordinary node's unconnected widget input in _emit - it does NOT
        # mean "no value". Both cases fall through to the same widget lookup.
        ordered = [(inp["name"], inp["type"]) for inp in sg_def["inputs"]]
        widget_names = [name for name, typ in ordered if is_widget_type(typ)]
        widget_values = instance_node.get("widgets_values") or []
        widget_value_by_name = dict(zip(widget_names, widget_values))
        return widget_value_by_name.get(boundary_name)


def find_prompt_nodes(flat_graph: dict):
    """Any node/input in the flattened graph literally named 'prompt' holding a
    plain string value (not a link) - the text-prompt injection point(s)."""
    candidates = []
    for node_id, node in flat_graph.items():
        for name, value in node["inputs"].items():
            if name == "prompt" and isinstance(value, str):
                candidates.append((node_id, name))
    return candidates
