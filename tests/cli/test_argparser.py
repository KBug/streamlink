from __future__ import annotations

import gettext
from argparse import SUPPRESS, Namespace
from pathlib import Path
from typing import Any
from unittest.mock import Mock, call

import pytest

from streamlink.plugin import Plugin, pluginargument
from streamlink.session import Streamlink
from streamlink_cli.argparser import (
    ArgumentParser,
    build_parser,
    setup_plugin_args,
    setup_plugin_options,
    setup_session_options,
)
from streamlink_cli.console import ConsoleUserInputRequester
from streamlink_cli.exceptions import StreamlinkCLIError
from streamlink_cli.main import main as streamlink_cli_main


@pytest.fixture(scope="module")
def parser():
    return build_parser()


class TestConfigFileArguments:
    @pytest.fixture()
    def parsed(self, request: pytest.FixtureRequest, parser: ArgumentParser, tmp_path: Path):
        content = "\n".join([
            "",
            " ",
            "# comment",
            "! comment",
            "invalid_option_format",
            *getattr(request, "param", []),
        ])

        config = tmp_path / "config"
        with config.open("w") as fd:
            fd.write(content)

        return parser.parse_args([f"@{config}"])

    @pytest.mark.parametrize("parsed", [[]], indirect=True)
    def test_nooptions(self, parsed: Namespace):
        assert parsed.ipv4 is None
        assert parsed.player_fifo is False
        assert parsed.player_args == ""
        assert parsed.title is None

    @pytest.mark.parametrize("parsed", [
        pytest.param(["4"], id="shorthand name"),
        pytest.param(["ipv4"], id="full name"),
    ], indirect=True)
    def test_alphanumerical(self, parsed: Namespace):
        assert parsed.ipv4 is True

    @pytest.mark.parametrize("parsed", [
        pytest.param(["n"], id="shorthand name"),
        pytest.param(["player-fifo"], id="full name"),
    ], indirect=True)
    def test_withoutvalue(self, parsed: Namespace):
        assert parsed.player_fifo is True

    @pytest.mark.parametrize("parsed", [
        pytest.param(["a=foo bar "], id="shorthand name with operator"),
        pytest.param(["a = foo bar "], id="shorthand name with operator and surrounding whitespace"),
        pytest.param(["a   foo bar "], id="shorthand name without operator"),
        pytest.param(["player-args=foo bar "], id="full name with operator"),
        pytest.param(["player-args = foo bar "], id="full name with operator and surrounding whitespace"),
        pytest.param(["player-args   foo bar "], id="full name without operator"),
    ], indirect=True)
    def test_withvalue(self, parsed: Namespace):
        assert parsed.player_args == "foo bar"

    @pytest.mark.parametrize("parsed", [
        pytest.param(["title="], id="operator"),
        pytest.param(["title ="], id="operator with leading whitespace"),
        pytest.param(["title = "], id="operator with surrounding whitespace"),
    ], indirect=True)
    def test_emptyvalue(self, parsed: Namespace):
        assert parsed.title == ""

    @pytest.mark.parametrize("parsed", [
        pytest.param(["http-header=foo=bar=baz", "http-header=FOO=BAR=BAZ"], id="With operator"),
        pytest.param(["http-header foo=bar=baz", "http-header FOO=BAR=BAZ"], id="Without operator"),
    ], indirect=True)
    def test_keyequalsvalue(self, parsed: Namespace):
        assert parsed.http_header == [("foo", "bar=baz"), ("FOO", "BAR=BAZ")]


class TestMatchArgumentOverride:
    @pytest.fixture(autouse=True)
    def _null_translations(self, monkeypatch: pytest.MonkeyPatch):
        null_translations = gettext.NullTranslations()
        monkeypatch.setattr("argparse._", null_translations.gettext)
        monkeypatch.setattr("argparse.ngettext", null_translations.ngettext)

    @pytest.fixture(scope="module")
    def parser(self):
        # TODO: py38 support end: set exit_on_error=False and capture argparse.ArgumentError
        parser = ArgumentParser()
        parser.add_argument("-a", "--one", dest="arg")
        parser.add_argument("-b", "--two", nargs=2)
        parser.add_argument("--one-or-more", nargs="+")

        return parser

    @pytest.mark.parametrize(
        "argv",
        [
            pytest.param(
                ["-a", "-v"],
                id="value-with-leading-dash-shorthand",
            ),
            pytest.param(
                ["--one", "-v"],
                id="value-with-leading-dash-full",
            ),
            pytest.param(
                ["--one=-v"],
                id="value-with-leading-dash-full-single-arg",
            ),
        ],
    )
    def test_match_argument(self, parser: ArgumentParser, argv: list):
        args, _ = parser.parse_known_args(argv)
        assert args.arg == "-v"

    @pytest.mark.parametrize(
        ("argv", "errormsg"),
        [
            pytest.param(
                ["--one"],
                "argument -a/--one: expected one argument\n",
                id="missing-value",
            ),
            pytest.param(
                ["--two"],
                "argument -b/--two: expected 2 arguments\n",
                id="missing-values",
            ),
            pytest.param(
                ["--one-or-more"],
                "argument --one-or-more: expected at least one argument\n",
                id="one-or-more",
            ),
        ],
    )
    def test_match_argument_error(self, capsys: pytest.CaptureFixture[str], parser: ArgumentParser, argv: list, errormsg: str):
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_known_args(argv)
        assert exc_info.value.code == 2
        assert capsys.readouterr().err.endswith(errormsg)


@pytest.mark.parametrize(("argv", "option", "expected"), [
    pytest.param(
        ["--locale", "xx_XX"],
        "locale",
        "xx_XX",
        id="Arg+value without mapper",
    ),
    pytest.param(
        ["--http-disable-dh"],
        "http-disable-dh",
        True,
        id="Arg with action=store_true",
    ),
    pytest.param(
        ["--http-no-ssl-verify"],
        "http-ssl-verify",
        False,
        id="Arg with action=store_false",
    ),
    pytest.param(
        ["--http-query-param", "foo=bar", "--http-query-param", "baz=qux"],
        "http-query-params",
        {"foo": "bar", "baz": "qux"},
        id="Arg+value with dict mapper",
    ),
    pytest.param(
        ["--http-ssl-cert-crt-key", "foo.crt", "bar.key"],
        "http-ssl-cert",
        ("foo.crt", "bar.key"),
        id="Arg+value with tuple mapper",
    ),
])
def test_setup_session_options(parser: ArgumentParser, session: Streamlink, argv: list, option: str, expected: Any):
    args = parser.parse_args(argv)
    setup_session_options(session, args)
    assert session.get_option(option) == expected


def test_setup_session_options_default_values(monkeypatch: pytest.MonkeyPatch, parser: ArgumentParser, session: Streamlink):
    mock_set_option = Mock()
    monkeypatch.setattr(session, "set_option", mock_set_option)
    args = parser.parse_args([])
    setup_session_options(session, args)
    assert session.options.options == session.options.defaults
    assert not mock_set_option.called, "Value of unset session-option arg must be None and must not call set_option()"


@pytest.mark.parametrize(("default", "new", "expected"), [
    pytest.param(False, None, False, id="Default False, unset"),
    pytest.param(True, None, True, id="Default True, unset"),
    pytest.param(False, False, False, id="Default False, set to False"),
    pytest.param(False, True, True, id="Default False, set to True"),
    pytest.param(True, False, False, id="Default True, set to False"),
    pytest.param(True, True, True, id="Default True, set to True"),
])
def test_setup_session_options_override(monkeypatch: pytest.MonkeyPatch, session: Streamlink, default, new, expected):
    arg = "NON_EXISTING_ARGPARSER_ARGUMENT"
    key = "NON-EXISTING-SESSION-OPTION-KEY"
    monkeypatch.setattr("streamlink_cli.argparser._ARGUMENT_TO_SESSIONOPTION", [(arg, key, None)])
    session.set_option(key, default)
    setup_session_options(session, Namespace(**{arg: new}))
    assert session.get_option(key) == expected


@pytest.mark.parametrize(
    ("namespace", "expected"),
    [
        pytest.param(Namespace(deprecated=None, new=123), 123, id="new-only"),
        pytest.param(Namespace(deprecated=123, new=None), 123, id="deprecated-only"),
        pytest.param(Namespace(deprecated=123, new=456), 456, id="new-overrides-deprecated"),
    ],
)
def test_setup_session_options_deprecation_override(
    monkeypatch: pytest.MonkeyPatch,
    session: Streamlink,
    namespace: Namespace,
    expected: int,
):
    arg_to_sessopt = [("deprecated", "option", None), ("new", "option", None)]
    monkeypatch.setattr("streamlink_cli.argparser._ARGUMENT_TO_SESSIONOPTION", arg_to_sessopt)
    setup_session_options(session, namespace)
    assert session.options.get_explicit("option") == expected


def test_cli_main_setup_session_options(monkeypatch: pytest.MonkeyPatch, parser: ArgumentParser, session: Streamlink):
    class StopTest(Exception):
        pass

    mock_setup_session_options = Mock()

    monkeypatch.setattr("sys.argv", [])
    monkeypatch.setattr("streamlink_cli.main.CONFIG_FILES", [])
    monkeypatch.setattr("streamlink_cli.main.logger", Mock())
    monkeypatch.setattr("streamlink_cli.main.streamlink", session)
    monkeypatch.setattr("streamlink_cli.main.build_parser", Mock(return_value=parser))
    monkeypatch.setattr("streamlink_cli.main.setup_streamlink", Mock())
    monkeypatch.setattr("streamlink_cli.main.setup_plugins", Mock())
    monkeypatch.setattr("streamlink_cli.main.log_root_warning", Mock())
    monkeypatch.setattr("streamlink_cli.main.log_current_versions", Mock())
    monkeypatch.setattr("streamlink_cli.main.log_current_arguments", Mock())
    monkeypatch.setattr("streamlink_cli.main.setup_session_options", mock_setup_session_options)
    monkeypatch.setattr("streamlink_cli.main.setup_signals", Mock(side_effect=StopTest))

    with pytest.raises(StopTest):
        streamlink_cli_main()

    assert mock_setup_session_options.call_count == 1, \
        "Has called setup_session_options() before setting up signals and running actual CLI code"
    assert mock_setup_session_options.call_args_list[0][0][0] is session
    assert isinstance(mock_setup_session_options.call_args_list[0][0][1], Namespace)


class TestSetupPluginArgsAndOptions:
    @pytest.fixture(autouse=True)
    def stdin(self, monkeypatch: pytest.MonkeyPatch):
        mock_stdin = Mock(isatty=Mock(return_value=True))
        monkeypatch.setattr("sys.stdin", mock_stdin)

        return mock_stdin

    @pytest.fixture()
    def console(self):
        return Mock(
            ask=Mock(return_value="answer"),
            askpass=Mock(return_value="password"),
        )

    @pytest.fixture()
    def parser(self):
        return ArgumentParser(add_help=False)

    @pytest.fixture()
    def plugin(self):
        # simple argument which requires namespace-name normalization
        @pluginargument("foo-bar")
        # argument with default value
        @pluginargument("baz", default=456)
        # suppressed argument
        @pluginargument("qux", default=789, help=SUPPRESS)
        # required argument with dependencies
        @pluginargument("user", required=True, requires=["pass", "captcha"])
        # sensitive argument (using console.askpass if unset)
        @pluginargument("pass", sensitive=True)
        # argument with custom prompt (using console.ask if unset)
        @pluginargument("captcha", prompt="CAPTCHA code")
        class FakePlugin(Plugin):
            def _get_streams(self):  # pragma: no cover
                pass

        return FakePlugin

    @pytest.fixture()
    def session(self, session: Streamlink, console: Mock, parser: ArgumentParser, plugin: type[Plugin]):
        session.set_option("user-input-requester", ConsoleUserInputRequester(console))
        session.plugins["mock"] = plugin

        setup_plugin_args(session, parser)

        return session

    def test_setup_arguments(self, session: Streamlink, parser: ArgumentParser, plugin: type[Plugin]):
        group_plugins = next((grp for grp in parser._action_groups if grp.title == "Plugin options"), None)  # pragma: no branch
        assert group_plugins is not None, "Adds the 'Plugin options' arguments group"
        assert group_plugins in parser.NESTED_ARGUMENT_GROUPS[None], "Adds the 'Plugin options' arguments group"

        group_plugin = next((grp for grp in parser._action_groups if grp.title == "Mock"), None)  # pragma: no branch
        assert group_plugin is not None, "Adds the 'Mock' arguments group"
        assert group_plugin in parser.NESTED_ARGUMENT_GROUPS[group_plugins], "Adds the 'Mock' arguments group"

        assert [
            item
            for action in parser._actions
            for item in action.option_strings
            if action.help != SUPPRESS
        ] == [
            "--mock-foo-bar",
            "--mock-baz",
            "--mock-user",
            "--mock-pass",
            "--mock-captcha",
        ], "Parser has all arguments registered"

    def test_setup_options_no_plugin_arguments(self, session: Streamlink, console: Mock):
        options = setup_plugin_options(session, Namespace(), "mock", Plugin)
        assert not options.defaults
        assert not options.options

        assert not console.ask.called
        assert not console.askpass.called

    def test_setup_options_no_user_input_requester(self, session: Streamlink, plugin: type[Plugin]):
        session.set_option("user-input-requester", None)
        with pytest.raises(RuntimeError) as exc_info:
            setup_plugin_options(session, Namespace(), "mock", plugin)
        assert str(exc_info.value) == "The Streamlink session is missing a UserInputRequester"

    def test_setup_options(self, session: Streamlink, plugin: type[Plugin], console: Mock):
        args = Namespace(
            mock_foo_bar=123,
            mock_baz=654,
            # mock_qux wouldn't be set by the parser if the argument is suppressed
            # its value will be ignored
            mock_qux=987,
            mock_user="username",
            mock_pass=None,
            mock_captcha=None,
        )
        options = setup_plugin_options(session, args, "mock", plugin)

        assert console.ask.call_args_list == [call("CAPTCHA code: ")]
        assert console.askpass.call_args_list == [call("Enter mock pass: ")]

        assert plugin.arguments
        arg_foo = plugin.arguments.get("foo-bar")
        arg_baz = plugin.arguments.get("baz")
        arg_qux = plugin.arguments.get("qux")
        assert arg_foo
        assert arg_baz
        assert arg_qux
        assert arg_foo.default is None
        assert arg_baz.default == 456
        assert arg_qux.default == 789

        assert options.get("foo-bar") == 123, "Overrides the default plugin-argument value"
        assert options.get("baz") == 654, "Uses the plugin-argument default value"
        assert options.get("qux") == 789, "Ignores values of suppressed plugin-arguments"
        assert options.get("pass") == "password"
        assert options.get("captcha") == "answer"

        options.clear()
        assert options.get("foo-bar") == arg_foo.default
        assert options.get("baz") == arg_baz.default
        assert options.get("qux") == arg_qux.default
        assert options.get("pass") is None
        assert options.get("captcha") is None

    def test_setup_options_no_tty(
        self,
        session: Streamlink,
        plugin: type[Plugin],
        stdin: Mock,
    ):
        stdin.isatty.return_value = False
        with pytest.raises(StreamlinkCLIError) as exc_info:
            setup_plugin_options(session, Mock(mock_user="username", mock_pass=None), "mock", plugin)
        assert str(exc_info.value) == "no TTY available"

    def test_setup_options_no_stdin(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: Streamlink,
        plugin: type[Plugin],
    ):
        monkeypatch.setattr("sys.stdin", None)
        with pytest.raises(StreamlinkCLIError) as exc_info:
            setup_plugin_options(session, Mock(mock_user="username", mock_pass=None), "mock", plugin)
        assert str(exc_info.value) == "no TTY available"
