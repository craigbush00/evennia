"""
(Better) MenuSystem

Evennia contribution - Griatch 2015


This implements a better menu system for Evennia. Contrary to the old
contrib menusystem, this is controlled from a simple module with
function definitions, rather than building a set of classes with
arguments.

To start the menu, just import the Menu class from this module,
and call

    from evennia.contrib.bettermenusystem import Menu

    Menu(caller, menu_module_path,
         startnode="start", allow_quit=True,
         cmdset_mergetype="Replace", cmdset_priority=1):

Where `caller` is the Object to use the menu on - it will get a new
cmdset while using the Menu. The menu_module_path is the python path
to a python module containing function defintions.  By adjusting the
keyword options of the Menu() initialization call you can start the
menu at different places in the menu definition file, adjust if the
menu command should overload the normal commands or not etc.

The menu is defined in a module with function defintions:

    def nodename1(caller):
        # code
        return text, options

The return values must be given in the above order, but each can be
given as None as well

    text (str or tuple): Text shown at this node. If a tuple, the second
        element in the tuple is a help text to display at this node when
        the user enters the menu help command there.
    helptext (str): Help text shown at this node.
    options (tuple): ( {'key': name,   # can also be a list of aliases
                        'desc': description, # option description
                        'goto': nodekey,  # node to go to when chosen
                        'exec': nodekey, # node or callback to trigger as callback when chosen
                        {...}, ...)

If key is not given, the option will automatically be identified by
its number 1..N.

Example:

```python

    # in menu_module.py

    def node1(caller):
        text = ("This is a node text",
                "This is help text for this node")
        options = ({"key": "testing",
                    "desc": "Select this to go to node 2"
                    "goto": "node2",
                    "exec": "callback1"},
                   {"desc": "Go to node 3."
                    "goto": "node3"} )
        return text, options

    def callback1(caller):
        # this is called when choosing the "testing" option in node1
        # (before going to node2). It needs not have return values.
        caller.msg("Callback called!")

    def node2(caller):
        text = '''
            This is node 2. It only allows you to go back
            to the original node1. This extra indent will
            be stripped. We don't include a help text.
            '''
        options = {"goto": "node1"}
        return text, options

    def node3(caller):
        text = "This ends the menu since there are no options."
        return text, None

```

When starting this menu with  `Menu(caller, "path.to.menu_module")`,
the first node will look something like this:

    This is a node text
    ______________________________________

    testing: Select this to go to node 2
    2: Go to node 3

Where you can both enter "testing" and "1" to select the first option.
If the client supports MXP, they may also mouse-click on "testing" to
do the same. When making this selection, a function "callback1" in the
same Using `help` will show the help text, otherwise a list of
available commands while in menu mode.

The menu tree is exited either by using the in-menu quit command or by
reaching a node without any options.


For a menu demo, import CmdTestDemo form this

"""

from textwrap import dedent
from inspect import isfunction, getargspec
from django.conf import settings
from evennia import syscmdkeys
from evennia import Command, CmdSet
from evennia.utils.evtable import EvTable
from evennia.utils.ansi import ANSIString
from evennia.utils.utils import mod_import, make_iter, pad, m_len

# read from protocol NAWS later?
_MAX_TEXT_WIDTH = settings.CLIENT_DEFAULT_WIDTH

_CMD_NOMATCH = syscmdkeys.CMD_NOMATCH
_CMD_NOINPUT = syscmdkeys.CMD_NOINPUT

# Return messages

_ERR_NOT_IMPLEMENTED = "Menu node '{nodename}' is not implemented. Make another choice."
_ERR_GENERAL = "Error in menu node '{nodename}'."
_ERR_NO_OPTION_DESC = "No description."
_HELP_FULL = "Commands: <menu option>, help, quit"
_HELP_NO_QUIT = "Commands: <menu option>, help"
_HELP_NO_OPTIONS = "Commands: help, quit"
_HELP_NO_OPTIONS_NO_QUIT = "Commands: help"



class MenuError(RuntimeError):
    """
    Error raised by menu when facing internal errors.

    """
    pass

#------------------------------------------------------------
#
# Menu command and command set
#
#------------------------------------------------------------

class CmdMenuNode(Command):
    """
    Menu options.

    """
    key = "look"
    aliases = ["l", _CMD_NOMATCH, _CMD_NOINPUT]
    locks = "cmd:all()"
    help_category = "Menu"

    def func(self):
        """
        Implement all menu commands.

        """
        caller = self.caller
        menu = caller.ndb._menutree

        if not menu:
            err = "Menu object not found as %s.ndb._menutree!" % (caller)
            self.caller.msg(err)
            raise MenuError(err)

        # flags and data
        raw_string = self.raw_string
        cmd = raw_string.strip().lower()
        options = menu.options
        allow_quit = menu.allow_quit
        default = menu.default

        if cmd in options:
            # this will overload the other commands
            # if it has the same name!
            goto, callback = options[cmd]
            if callback:
                menu.callback(callback, raw_string)
            if goto:
                menu.goto(goto, raw_string)
        elif cmd in ("look", "l"):
            caller.msg(menu.nodetext)
        elif cmd in ("help", "h"):
            caller.msg(menu.helptext)
        elif allow_quit and cmd in ("quit", "q", "exit"):
            menu.close_menu()
            caller.execute_cmd("look")
        elif default:
            goto, callback = default
            if callback:
                menu.callback(callback, raw_string)
            if goto:
                menu.goto(goto, raw_string)
        else:
            caller.msg("Choose an option or try 'help'.")

        if not (options or default):
            # no options - we are at the end of the menu.
            menu.close_menu()
            caller.execute_cmd("looK")


class MenuCmdSet(CmdSet):
    """
    The Menu cmdset replaces the current cmdset.

    """
    key = "menu_cmdset"
    priority = 1
    mergetype = "Replace"
    no_objs = True
    no_exits = True
    no_channels = False

    def at_cmdset_creation(self):
        """
        Called when creating the set.
        """
        self.add(CmdMenuNode())

#------------------------------------------------------------
#
# Menu main class
#
#------------------------------------------------------------

class Menu(object):
    """
    This object represents an operational menu. It is initialized from
    a menufile.py instruction.

    """
    def __init__(self, caller, menufile, startnode="start",
                 allow_quit=True, cmdset_mergetype="Replace", cmdset_priority=1):
        """
        Initialize the menu tree and start the caller onto the first node.

        Args:
            caller (str): The user of the menu.
            menufile (str): The full or relative path to the menufile.
            startnode (str, optional): The starting node in the menufile.
            allow_quit (bool, optional): Allow user to use quit or
                exit to leave the menu at any point. Recommended during
                development!
            cmdset_mergetype (str, optional): 'Replace' (default) means the menu
                commands will be exclusive - no other normal commands will
                be usable while the user is in the menu. 'Union' means the
                menu commands will be integrated with the existing commands
                (it will merge with `merge_priority`), if so, make sure that
                the menu's command names don't collide with existing commands
                in an unexpected way. Also the CMD_NOMATCH and CMD_NOINPUT will
                be overloaded by the menu cmdset. Other cmdser mergetypes
                has little purpose for the menu.
            cmdset_priority (int, optional): The merge priority for the
                menu command set. The default (1) is usually enough for most
                types of menus.
        Raises:
            MenuError: If the start/end node is not found in menu tree.

        """
        self._caller = caller
        self._startnode = startnode
        self._menutree = self._parse_menufile(menufile)

        if startnode not in self._menutree:
            raise MenuError("Start node '%s' not in menu tree!" % startnode)

        # variables made available to the command
        self.allow_quit = allow_quit
        self.default = None
        self.nodetext = None
        self.helptext = None
        self.options = None

        # store ourself on the object
        self._caller.ndb._menutree = self

        # set up the menu command on the caller
        menu_cmdset = MenuCmdSet()
        menu_cmdset.mergetype = str(cmdset_mergetype).lower().capitalize() or "Replace"
        menu_cmdset.priority = int(cmdset_priority)
        self._caller.cmdset.add(menu_cmdset)
        # start the menu
        self.goto(self._startnode, "")

    def _parse_menufile(self, menufile):
        """
        Parse a menufile, split it into #node sections, convert
        each to an executable python code and store in a dictionary map.

        Args:
            menufile (str or module): The python.path to the menufile,
                or the python module itself.

        Returns:
            menutree (dict): A {nodekey: func}

        """
        module = mod_import(menufile)
        return dict((key, func) for key, func in module.__dict__.items()
                    if isfunction(func) and not key.startswith("_"))

    def _format_node(self, nodetext, optionlist):
        """
        Format the node text + option section

        Args:
            nodetext (str): The node text
            optionlist (list): List of (key, desc) pairs.

        Returns:
            string (str): The options section, including
                all needed spaces.

        Notes:
            This will adjust the columns of the options, first to use
            a maxiumum of 4 rows (expanding in columns), then gradually
            growing to make use of the screen space.

        """
        #
        # handle the node text
        #

        nodetext = dedent(nodetext).strip()

        nodetext_width_max = max(m_len(line) for line in nodetext.split("\n"))

        if not optionlist:
            # return the node text "naked".
            separator1 = "_" * nodetext_width_max + "\n\n" if nodetext_width_max else ""
            separator2 = "\n" if nodetext_width_max else "" + "_" * nodetext_width_max
            return separator1 + nodetext + separator2

        #
        # handle the options
        #

        # column separation distance
        colsep = 4

        nlist = len(optionlist)

        # get the widest option line in the table.
        table_width_max = -1
        table = []
        for key, desc in optionlist:
            table_width_max = max(table_width_max,
                                  max(m_len(p) for p in key.split("\n")) +
                                  max(m_len(p) for p in desc.split("\n")) + colsep)
            table.append(ANSIString(" {lc%s{lt{w%s{n{le: %s" % (key, key, desc)))

        ncols = (_MAX_TEXT_WIDTH // table_width_max) + 1 # number of ncols
        nlastcol = nlist % ncols # number of elements left in last row

        # get the amount of rows needed (start with 4 rows)
        nrows = 4
        while nrows * ncols < nlist:
            nrows += 1
        ncols = nlist // nrows # number of full columns
        nlastcol = nlist % nrows # number of elements in last column

        # get the final column count
        ncols = ncols + 1 if nlastcol > 0 else ncols
        if ncols > 1:
            # only extend if longer than one column
            table.extend([" " for i in xrange(nrows-nlastcol)])

        # build the actual table grid
        table = [table[icol*nrows:(icol*nrows) + nrows] for icol in xrange(0, ncols)]

        # adjust the width of each column
        total_width = 0
        for icol in xrange(len(table)):
            col_width = max(max(m_len(p) for p in part.split("\n")) for part in table[icol]) + colsep
            table[icol] = [pad(part, width=col_width + colsep, align="l") for part in table[icol]]
            total_width += col_width

        # format the table into columns
        table = EvTable(table=table, border="none")

        # build the page
        total_width = max(total_width, nodetext_width_max)
        separator1 = "_" * total_width + "\n\n" if nodetext_width_max else ""
        separator2 = "\n" + "_" * total_width + "\n\n" if total_width else ""
        return separator1 + nodetext + separator2 + unicode(table)

    def _execute_node(self, nodename, raw_string):
        """
        Execute a node.

        Args:
            nodename (str): Name of node.
            raw_string (str): The raw default string entered on the
                previous node (only used if the node accepts it as an
                argument)

        Returns:
            nodetext, options (tuple): The node text (a string or a
                tuple and the options tuple, if any.

        """
        try:
            node = self._menutree[nodename]
        except KeyError:
            self._caller.msg(_ERR_NOT_IMPLEMENTED.format(nodename=nodename))
            raise MenuError
        try:
            # the node should return data as (text, options)
            if len(getargspec(node).args) > 1:
                # a node accepting raw_string
                nodetext, options = node(self._caller, raw_string)
            else:
                # a normal node, only accepting caller
                nodetext, options = node(self._caller)
        except KeyError:
            self._caller.msg(_ERR_NOT_IMPLEMENTED.format(nodename=nodename))
            raise MenuError
        except Exception:
            self._caller.msg(_ERR_GENERAL.format(nodename=nodename))
            raise
        return nodetext, options


    def callback(self, nodename, raw_string):
        """
        Run a node as a callback. This makes no use of the return
        values from the node.

        Args:
            nodename (str): Name of node.
            raw_string (str): The raw default string entered on the
                previous node (only used if the node accepts it as an
                argument)

        """
        if callable(nodename):
            # this is a direct callable - execute it directly
            try:
                if len(getargspec(nodename).args) > 1:
                    # callable accepting raw_string
                    nodename(self._caller, raw_string)
                else:
                    # normal callable, only the caller as arg
                    nodename(self._caller)
            except Exception:
                self._caller.msg(_ERR_GENERAL.format(nodename=nodename))
                raise
        else:
            # nodename is a string; lookup as node
            try:
                # execute the node; we make no use of the return values here.
                self._execute_node(nodename, raw_string)
            except MenuError:
                return

    def goto(self, nodename, raw_string):
        """
        Run a node by name

        Args:
            nodename (str): Name of node.
            raw_string (str): The raw default string entered on the
                previous node (only used if the node accepts it as an
                argument)

        """
        try:
            # execute the node, make use of the returns.
            nodetext, options = self._execute_node(nodename, raw_string)
        except MenuError:
            return

        # validation of the node return values
        helptext = ""
        if hasattr(nodetext, "__iter__"):
            if len(nodetext) > 1:
                nodetext, helptext = nodetext[:2]
            else:
                nodetext = nodetext[0]
        nodetext = str(nodetext) or ""
        options = [options] if isinstance(options, dict) else options

        # this will be displayed in the given order
        display_options = []
        # this is used for lookup
        self.options = {}
        self.default = None
        if options:
            for inum, dic in enumerate(options):
                # fix up the option dicts
                keys = make_iter(dic.get("key"))
                if "_default" in keys:
                    keys = [key for key in keys if key != "_default"]
                    desc = dic.get("desc", dic.get("text", _ERR_NO_OPTION_DESC).strip())
                    goto, execute = dic.get("goto", None), dic.get("exec", None)
                    self.default = (goto, execute)
                else:
                    keys = list(make_iter(dic.get("key", str(inum+1).strip()))) + [str(inum+1)]
                    desc = dic.get("desc", dic.get("text", _ERR_NO_OPTION_DESC).strip())
                    goto, execute = dic.get("goto", None), dic.get("exec", None)

                if keys:
                    display_options.append((keys[0], desc))
                    for key in keys:
                        if goto or execute:
                            self.options[key.strip().lower()] = (goto, execute)

        self.nodetext = self._format_node(nodetext, display_options)

        # handle the helptext
        if helptext:
            self.helptext = helptext
        elif options:
            self.helptext = _HELP_FULL if self.allow_quit else _HELP_NO_QUIT
        else:
            self.helptext = _HELP_NO_OPTIONS if self.allow_quit else _HELP_NO_OPTIONS_NO_QUIT

        self._caller.execute_cmd("look")

    def close_menu(self):
        """
        Shutdown menu; occurs when reaching the end node.
        """
        self._caller.cmdset.remove(MenuCmdSet)
        del self._caller.ndb._menutree



#------------------------------------------------------------
#
# test menu strucure and testing command
#
#------------------------------------------------------------

def test_start_node(caller):
    text = """
    This is an example menu.

    If you enter anything except the valid options, your input will be
    recorded and you will be brought to a menu entry showing your
    input.

    Select options or use 'quit' to exit the menu.
    """
    options = ({"key": ("{yS{net", "s"),
                "desc": "Set an attribute on yourself.",
                "exec": lambda caller: caller.attributes.add("menuattrtest", "Test value"),
                "goto": "test_set_node"},
               {"key": ("{yV{niew", "v"),
                "desc": "View your own name",
                "goto": "test_view_node"},
               {"key": ("{yQ{nuit", "quit", "q", "Q"),
                "desc": "Quit this menu example.",
                "goto": "test_end_node"},
               {"key": "_default",
                "goto": "test_displayinput_node"})
    return text, options


def test_set_node(caller):
    text = ("""
    The attribute 'menuattrtest' was set to

            {w%s{n

    (check it with examine after quitting the menu).

    This node's has only one option, and one of its key aliases is the
    string "_default", meaning it will catch any input, in this case
    to return to the main menu.  So you can e.g. press <return> to go
    back now.
    """ % caller.db.menuattrtest,
    # optional help text for this node
    """
    This is the help entry for this node. It is created by returning
    the node text as a tuple - the second string in that tuple will be
    used as the help text.
    """)

    options = {"key": ("back (default)", "_default"),
               "desc": "back to main",
               "goto": "test_start_node"}
    return text, options


def test_view_node(caller):
    text = """
    Your name is {g%s{n!

    This node's option has no explicit key (nor the "_default" key
    set), and so gets assigned a number automatically. You can infact
    -always- use numbers (1...N) to refer to listed options also if you
    don't see a string option key (try it!).
    """ % caller.key
    options = {"desc": "back to main",
               "goto": "test_start_node"}
    return text, options


def  test_displayinput_node(caller, raw_string):
    text = """
    You entered the text:

        "{w%s{n"

    ... which could now be handled or stored here in some way if this
    was not just an example.

    This node has an option with a single alias "_default", which
    makes it hidden from view. It catches all input (except the
    in-menu help/quit commands) and will, in this case, bring you back
    to the start node.
    """ % raw_string
    options = {"key": "_default",
              "goto": "test_start_node"}
    return text, options


def test_end_node(caller):
    text = """
    This is the end of the menu and since it has no options the menu
    will exit here, followed by a call of the "look" command.
    """
    return text, None



class CmdTestMenu(Command):
    """
    Test menu

    Usage:
      testmenu <menumodule>

    Starts a demo menu from a menu node definition module.

    """
    key = "testmenu"

    def func(self):

        if not self.args:
            self.caller.msg("Usage: testmenu <menumodule>")
            return
        # start menu
        Menu(self.caller, self.args.strip(), startnode="test_start_node", cmdset_mergetype="Replace")