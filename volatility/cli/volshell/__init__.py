import argparse
import json
import logging
import os
import sys
from urllib import request

import volatility.framework
import volatility.plugins
from volatility import cli, framework
from volatility.cli import text_renderer
from volatility.cli.volshell import shellplugin, windows
from volatility.framework import constants, contexts, automagic, interfaces

# Make sure we log everything
vollog = logging.getLogger()
vollog.setLevel(0)
# Trim the console down by default
console = logging.StreamHandler()
console.setLevel(logging.WARNING)
formatter = logging.Formatter('%(levelname)-8s %(name)-12s: %(message)s')
console.setFormatter(formatter)
vollog.addHandler(console)


class VolShell(cli.CommandLine):
    """Program to allow interactive """

    def run(self):
        sys.stdout.write("Volshell (Volatility Framework) {}\n".format(constants.PACKAGE_VERSION))

        volatility.framework.require_interface_version(0, 0, 0)

        parser = argparse.ArgumentParser(prog = 'volshell',
                                         description = "A tool for interactivate forensic analysis of memory images")
        parser.add_argument("-c", "--config", help = "Load the configuration from a json file", default = None,
                            type = str)
        parser.add_argument("-e", "--extend", help = "Extend the configuration with a new (or changed) setting",
                            default = None, action = 'append')
        parser.add_argument("-p", "--plugins", help = "Semi-colon separated list of paths to find plugins",
                            default = "", type = str)
        parser.add_argument("-v", "--verbosity", help = "Increase output verbosity", default = 0, action = "count")
        parser.add_argument("-o", "--output-dir", help = "Directory in which to output any generated files",
                            default = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')), type = str)
        parser.add_argument("--log", help = "Log output to a file as well as the console", default = None, type = str)
        parser.add_argument("-f", metavar = "FILE", default = None, type = str,
                            help = "Shorthand for --single-location=file://FILE if single-location is not defined")

        # Volshell specific flags
        parser.add_argument("-w", "--windows", default = False, action = "store_true", help = "Run a Windows volshell")
        parser.add_argument("-l", "--linux", default = False, action = "store_true", help = "Run a Linux volshell")

        # We have to filter out help, otherwise parse_known_args will trigger the help message before having
        # processed the plugin choice or had the plugin subparser added.
        known_args = [arg for arg in sys.argv if arg != '--help' and arg != '-h']
        partial_args, _ = parser.parse_known_args(known_args)
        if partial_args.plugins:
            volatility.plugins.__path__ = partial_args.plugins.split(";") + constants.PLUGINS_PATH

        if partial_args.log:
            file_logger = logging.FileHandler(partial_args.log)
            file_logger.setLevel(0)
            file_formatter = logging.Formatter(datefmt = '%y-%m-%d %H:%M:%S',
                                               fmt = '%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
            file_logger.setFormatter(file_formatter)
            vollog.addHandler(file_logger)
            vollog.info("Logging started")

        # Do the initialization
        ctx = contexts.Context()  # Construct a blank context
        framework.import_files(volatility.plugins)  # Will not log as console's default level is WARNING
        automagics = automagic.available(ctx)

        # Initialize the list of plugins in case volshell needs it
        framework.list_plugins()

        seen_automagics = set()
        configurables_list = {}
        for amagic in automagics:
            if amagic in seen_automagics:
                continue
            seen_automagics.add(amagic)
            if isinstance(amagic, interfaces.configuration.ConfigurableInterface):
                self.populate_requirements_argparse(parser, amagic.__class__)
                configurables_list[amagic.__class__.__name__] = amagic

        # We don't list plugin arguments, because they can be provided within python
        volshell_plugin_list = {'generic': shellplugin.Volshell,
                                'windows': windows.Volshell}
        for plugin in volshell_plugin_list:
            subparser = parser.add_argument_group(title = plugin.capitalize(),
                                                  description = "Configuration options based on {} options".format(
                                                      plugin.capitalize()))
            self.populate_requirements_argparse(subparser, volshell_plugin_list[plugin])
            configurables_list[plugin] = volshell_plugin_list[plugin]

        # Run the argparser
        args = parser.parse_args()
        if args.verbosity < 3:
            console.setLevel(30 - (args.verbosity * 10))
        else:
            console.setLevel(10 - (args.verbosity - 2))

        vollog.log(constants.LOGLEVEL_VVV, "Cache directory used: {}".format(constants.CACHE_PATH))

        plugin = shellplugin.Volshell
        if args.windows:
            plugin = windows.Volshell

        plugin_config_path = interfaces.configuration.path_join('plugins', plugin.__name__)

        # Special case the -f argument because people use is so frequently
        # It has to go here so it can be overridden by single-location if it's defined
        # NOTE: This will *BREAK* if LayerStacker, or the automagic configuration system, changes at all
        ###
        if args.f:
            file_name = os.path.abspath(args.f)
            if not os.path.exists(file_name):
                vollog.log(logging.INFO, "File does not exist: {}".format(file_name))
            else:
                single_location = "file:" + request.pathname2url(file_name)
                ctx.config['automagic.LayerStacker.single_location'] = single_location

        # UI fills in the config, here we load it from the config file and do it before we process the CL parameters
        if args.config:
            with open(args.config, "r") as f:
                json_val = json.load(f)
                ctx.config.splice(plugin_config_path, interfaces.configuration.HierarchicalDict(json_val))

        self.populate_config(ctx, configurables_list, args, plugin_config_path)

        if args.extend:
            for extension in args.extend:
                if '=' not in extension:
                    raise ValueError(
                        "Invalid extension (extensions must be of the format \"conf.path.value='value'\")")
                address, value = extension[:extension.find('=')], json.loads(extension[extension.find('=') + 1:])
                ctx.config[address] = value

        # It should be up to the UI to determine which automagics to run, so this is before BACK TO THE FRAMEWORK
        automagics = automagic.choose_automagic(automagics, plugin)
        self.output_dir = args.output_dir

        ###
        # BACK TO THE FRAMEWORK
        ###
        try:
            constructed = self.run_plugin(ctx,
                                          automagics,
                                          plugin,
                                          plugin_config_path)

            # Construct and run the plugin
            text_renderer.QuickTextRenderer().render(constructed.run())
        except cli.UnsatisfiedException as excp:
            parser.exit(1, "Unable to validate the plugin requirements: {}\n".format(excp.unsatisfied))


def main():
    """A convenience function for constructing and running the :class:`CommandLine`'s run method"""
    VolShell().run()