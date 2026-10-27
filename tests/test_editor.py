import pytest
from acenix.editor import Editor, tokenize
from acenix.errors import ConflictError

SOURCE = """{ config, pkgs, ... }: {
  # Keep this comment.
  networking.hostName = "old"; # Inline comment.
  services = { openssh.enable = false; printing.enable = true; };
  imports = [ ./hardware-configuration.nix ];
  environment.systemPackages = with pkgs; [ vim (pkgs.foo.override { flag = true; }) ];
}
"""


def test_scalar_edit_preserves_bytes():
    result = Editor(SOURCE).set("networking.hostName", "new")
    assert result == SOURCE.replace('"old"', '"new"')
    assert Editor(result).set("networking.hostName", "new") == result


def test_nested_and_new():
    result = Editor(SOURCE).set("services.openssh.enable", True)
    assert "openssh.enable = true;" in result
    assert "printing.enable = true;" in result
    result = Editor(result).set("services.nginx.enable", True)
    assert "nginx.enable = true;" in result
    assert len(Editor(result).bindings) > len(Editor(SOURCE).bindings)


@pytest.mark.parametrize(
    "expression",
    [
        'lib.mkDefault "old"',
        'lib.mkForce "old"',
        '"${config.foo}"',
        'let x="old"; in x',
        'if true then "old" else "new"',
    ],
)
def test_refuse_computed_value(expression):
    text = "{ networking.hostName = " + expression + "; }"
    with pytest.raises(ConflictError):
        Editor(text).set("networking.hostName", "new")


@pytest.mark.parametrize(
    "text",
    ["let x=1; in {}", "{ inherit networking; }", "{ networking = someFunction {}; }"],
)
def test_refuse_semantic_ambiguity(text):
    with pytest.raises(ConflictError):
        Editor(text).set("networking.hostName", "new")


def test_packages_preserve_external_expressions():
    result = Editor(SOURCE).packages(["firefox"], ["vim"])
    assert "pkgs.firefox" in result and "[ vim " not in result
    assert "(pkgs.foo.override { flag = true; })" in result
    assert Editor(result).packages(["firefox"], []) == result


def test_new_package_binding_does_not_edit_comment():
    text = "{ pkgs, ... }: { # environment.systemPackages = [  ];\n }"
    result = Editor(text).packages(["git"], [])
    assert "# environment.systemPackages = [  ];" in result
    assert "environment.systemPackages = [ pkgs.git ];" in result


@pytest.mark.parametrize(
    "expression", ["with pkgs; base ++ [ vim ]", "lib.mkAfter [ pkgs.vim ]"]
)
def test_computed_package_lists(expression):
    with pytest.raises(ConflictError):
        Editor(
            "{pkgs,...}: {environment.systemPackages = " + expression + ";}"
        ).packages(["git"], [])


def test_interpolations_and_indented_strings_are_single_tokens():
    text = """{ x = "hello ${if true then "a" else "b"}"; y = ''a; { } ${"b"}''; }"""
    assert len([t for t in tokenize(text) if t.text == ";"]) == 2
    assert Editor(text).set("new.option", True).endswith("}")


def test_inherited_attrset_not_literal():
    with pytest.raises(ConflictError):
        Editor("{x = { inherit y; };}").set("x", {})


def test_quoted_native_attribute_edits_preserve_names():
    source = '{ services.nginx.virtualHosts."example.org".root = "/old"; }'
    result = Editor(source).set(
        'services.nginx.virtualHosts."example.org".root', "/new"
    )
    assert result == source.replace("/old", "/new")
    result = Editor(result).set('environment.etc."file:with.dots".text', "hello")
    assert 'environment.etc."file:with.dots".text = "hello";' in result


def test_even_backslashes_do_not_hide_executable_interpolation():
    from acenix.editor import literal_string

    expression = '"' + "\\" * 2 + '${dangerous}"'
    assert literal_string(expression) is None
    with pytest.raises(ConflictError):
        Editor("{ x = " + expression + "; }").set("x", "new")


def test_escaped_literal_interpolation_remains_editable():
    source = '{ x = "\\${literal}"; }'
    assert Editor(source).set("x", "new") == '{ x = "new"; }'


def test_expanded_module_config_edits_do_not_touch_option_declarations():
    source = '{ pkgs, lib, ... }: { options.custom.value = lib.mkOption { default = "default"; }; config = { custom.value = "old"; environment.systemPackages = [ pkgs.vim ]; }; }'
    changed = Editor(source).set("custom.value", "new")
    assert changed == source.replace('"old"', '"new"')
    added = Editor(changed).set("custom.enabled", True)
    assert "enabled = true" in added
    assert 'default = "default"' in added
    assert "pkgs.hello" in Editor(added).packages(["hello"], [])
