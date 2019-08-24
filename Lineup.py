import re
from itertools import groupby, chain

import sublime
import sublime_plugin


DEFAULT_MULTI_MATCH_POLICY = 'first'
DEFAULT_PRE_SPACE_POLICY = 'remove'
DEFAULT_ADD_POST_SPACE = False

def line_is_empty(line):
    return line.a == line.b


def exclude_nones(xs):
    return [x for x in xs if x is not None]


def add_spaces(view, edit, pt, length):
    view.insert(edit, pt, ' ' * length)


def add_or_remove_space(view, edit, pt, length):
    if length >= 0:
        add_spaces(view, edit, pt, length)
        return length
    else:
        safe_start_pt = pt - 1
        while view.substr(safe_start_pt) == ' ' and safe_start_pt >= pt + length:
            safe_start_pt -= 1
        safe_start_pt += 1
        view.erase(edit, sublime.Region(safe_start_pt, pt))
        return safe_start_pt - pt


def do_padding(view, edit, pads):
    total_length = 0
    # Ensure our paddings are applied in increasing order.
    for (pt, length) in sorted(pads, key=lambda p: p[0]):
        pt += total_length
        mod_length = add_or_remove_space(view, edit, pt, length)
        total_length += mod_length
    return any(length != 0 for (_, length) in pads)


def get_paddings(view, points, col):
    for pt in points:
        length = col - view.rowcol(pt)[1]
        yield (pt, length)


def pad_to_col(view, edit, points, col):
    pads = list(get_paddings(view, points, col))
    return do_padding(view, edit, pads)


def get_col_extremum(view, points, do_min):
    f = min if do_min else max
    return (
        f(view.rowcol(pt)[1] for pt in points)
        if points
        else 0
    )


def get_line_left_edge(view, line_nr):
    pt = view.text_point(line_nr, 0)
    line = view.line(pt)
    for pt in range(line.a, line.b):
        char = view.substr(pt)
        if char != ' ':
            return pt
    # Ignore lines with only spaces.
    return None


def do_left_align(view, edit, line_nrs, go_left):
    # Find the left-most edges
    points = exclude_nones([
        get_line_left_edge(view, line_nr)
        for line_nr in line_nrs
    ])
    align_col = get_col_extremum(view, points, do_min=go_left)
    return pad_to_col(view, edit, points, align_col)


def analyze_match_align(view, line_nrs, matchers,
                        pre_space_policy, add_post_space,
                        prefixes,
                        multi_match_policy,
                        go_left):
    points = []
    align_points = []
    post_pads = []
    matches_iter = groupby(
        sorted(chain(*[
            view.find_all(m, flags=sublime.LITERAL)
            for m in matchers
        ])),
        key=lambda r: view.rowcol(r.a)[0]
    )
    for line_nr, matches_iter in matches_iter:
        # Ignore matches outside our lines of interest.
        if line_nr not in line_nrs:
            continue
        matches = list(matches_iter)

        if len(matches) > 1:
            if multi_match_policy == 'first':
                match_region = matches[0]
            elif multi_match_policy == 'last':
                match_region = matches[-1]
            elif multi_match_policy == 'skip':
                continue
            else:
                view.window().status_message('Unknown multi-match policy: {}'
                                             .format(multi_match_policy))
                return
        else:
            match_region = matches[0]

        matching_char_pt = match_region.a

        insert_pt = matching_char_pt
        # If the match follows a prefix, bring the first character forward
        # also.
        if view.substr(insert_pt - 1) in prefixes:
            insert_pt -= 1

        # Find the point at which to align.
        align_pt = insert_pt
        # Prune the pre-space.
        if pre_space_policy in ['remove', 'one', 'two', 'four']:
            # But only if we have a previous non-space character to prune up
            # to.
            if insert_pt > get_line_left_edge(view, line_nr):
                print(pre_space_policy)
                while view.substr(align_pt - 1) == ' ':
                    align_pt -= 1
                if pre_space_policy == 'one':
                    align_pt += 1
                elif pre_space_policy == 'two':
                    align_pt += 2
                elif pre_space_policy == 'four':
                    align_pt += 4
        elif pre_space_policy == 'keep':
            pass
        else:
            view.window().status_message('Unknown pre-space policy: {}'
                                         .format(pre_space_policy))
            return

        points.append(insert_pt)
        align_points.append(align_pt)

        # If we want a space after the match,
        # and the next character isn't already a space,
        # and the match isn't at the end of a line (to avoid adding a trailing
        # space).
        if (add_post_space
                and view.substr(match_region.b) != ' '
                and view.rowcol(match_region.b + 1)[0] == line_nr):
            post_pads.append((match_region.b, 1))

    align_col = get_col_extremum(view, align_points, do_min=go_left)
    align_paddings = list(get_paddings(view, points, align_col))
    return align_paddings + post_pads


def do_match_align(view, edit, line_nrs, alignments, match='auto'):
    if match == 'auto':
        pad_sets = []
        for match, opts in alignments.items():
            pre_space_policy = opts.get('pre_space_policy', DEFAULT_PRE_SPACE_POLICY)
            add_post_space = opts.get('add_post_space', DEFAULT_ADD_POST_SPACE)
            matchers = opts.get('matches', [])
            prefixes = opts.get('prefixes', [])
            multi_match_policy = opts.get('multi_match_policy',
                                          DEFAULT_MULTI_MATCH_POLICY)
            go_left = opts.get('bias_left', False)
            pads = analyze_match_align(
                view, line_nrs, matchers,
                pre_space_policy, add_post_space, prefixes,
                multi_match_policy,
                go_left,
            )
            pad_sets.append(pads)
        def diff_size(pads):
            return sum(abs(ln) for (_, ln) in pads)
        pad_set_to_do = max(pad_sets, key=diff_size)
    else:
        opts = alignments[match]
        pre_space_policy = opts.get('pre_space_policy', DEFAULT_PRE_SPACE_POLICY)
        add_post_space = opts.get('add_post_space', DEFAULT_ADD_POST_SPACE)
        matchers = opts.get('matches', [])
        prefixes = opts.get('prefixes', [])
        multi_match_policy = opts.get('multi_match_policy',
                                      DEFAULT_MULTI_MATCH_POLICY)
        go_left = opts.get('bias_left', False)
        pad_set_to_do = analyze_match_align(
            view, line_nrs, matchers,
            pre_space_policy, add_post_space, prefixes,
            multi_match_policy,
            go_left,
        )
    do_padding(view, edit, pad_set_to_do)


def get_line_nrs(view, region):
    return [
        view.rowcol(line.a)[0]
        for line in view.lines(region)
    ]


class LineupLeftAlign(sublime_plugin.TextCommand):

    def run(self, edit, **args):
        go_left = args.get('bias_left', False)
        for region in self.view.sel():
            line_nrs = get_line_nrs(self.view, region)
            do_left_align(
                self.view, edit, line_nrs,
                go_left=go_left,
            )


class LineupMatchAlign(sublime_plugin.TextCommand):

    def run(self, edit, **args):
        match = args.get('match_name', 'auto')
        settings = self.view.settings()
        alignments = settings.get('alignments', {})
        for region in self.view.sel():
            line_nrs = get_line_nrs(self.view, region)
            do_match_align(self.view, edit, line_nrs, alignments, match)


class LineupManualMatch(sublime_plugin.WindowCommand):

    def run(self):
        self._items = [
            [match]
            for match in self.window.active_view().settings().get('alignments', {})
        ]

        if self._items:
            self.window.show_quick_panel(self._items, self._on_done)
        else:
            self.window.status_message("No alignments available")

    def _on_done(self, index):
        if index > -1:
            match_name = self._items[index][0]
            self.window.status_message("Aligning on {}".format(match_name))
            self.window.run_command("lineup_match_align",
                                    args={'match_name': match_name})
