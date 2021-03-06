#    Copyright (c) 2014 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
  CLI interface for murano management.
"""

import sys
import traceback

from oslo.config import cfg

import muranoapi
from muranoapi.common import consts
from muranoapi.db.catalog import api as db_catalog_api
from muranoapi.db import session as db_session
from muranoapi.openstack.common.db import exception as db_exception
from muranoapi.openstack.common import log as logging
from muranoapi.packages import application_package


CONF = cfg.CONF
LOG = logging.getLogger(__name__)


# TODO(ruhe): proper error handling
def do_db_sync():
    """
    Place a database under migration control and upgrade,
    creating first if necessary.
    """
    db_session.db_sync()


def _do_import_package(_dir, categories):
    LOG.info("Going to import Murano package from {0}".format(_dir))
    pkg = application_package.load_from_dir(_dir)
    package = {
        'fully_qualified_name': pkg.full_name,
        'type': pkg.package_type,
        'author': pkg.author,
        'name': pkg.display_name,
        'description': pkg.description,
        # note: we explicitly mark all the imported packages as public,
        # until a parameter added to control visibility scope of a package
        'is_public': True,
        'tags': pkg.tags,
        'logo': pkg.logo,
        'ui_definition': pkg.raw_ui,
        'class_definitions': pkg.classes,
        'archive': pkg.blob,
        'categories': categories or []
    }

    # note(ruhe): the second parameter is tenant_id
    # it is a required field in the DB, that's why we pass an empty string
    result = db_catalog_api.package_upload(package, '')
    LOG.info("Finished import of package {0}".format(result.id))


# TODO(ruhe): proper error handling
def do_import_package():
    """
    Import Murano package from local directory.
    """
    _do_import_package(CONF.command.directory, CONF.command.categories)


def do_list_categories():
    categories = db_catalog_api.category_get_names()

    if categories:
        print(">> Murano package categories:")
        for c in categories:
            print("* {0}".format(c))
    else:
        print("No categories were found")


def do_add_category():
    category_name = CONF.command.category_name

    try:
        db_catalog_api.category_add(category_name)
        print(">> Successfully added category {0}".format(category_name))
    except db_exception.DBDuplicateEntry:
        print(">> ERROR: Category '{0}' already exists".format(category_name))


def add_command_parsers(subparsers):
    parser = subparsers.add_parser('db-sync')
    parser.set_defaults(func=do_db_sync)
    parser.add_argument('version', nargs='?')
    parser.add_argument('current_version', nargs='?')

    parser = subparsers.add_parser('import-package')
    parser.set_defaults(func=do_import_package)
    parser.add_argument('directory',
                        help='A directory with Murano package.')

    parser.add_argument('-c', '--categories',
                        choices=consts.CATEGORIES,
                        nargs='*',
                        help='An optional list of categories this package '
                             'to be assigned to.')

    parser = subparsers.add_parser('category-list')
    parser.set_defaults(func=do_list_categories)

    parser = subparsers.add_parser('category-add')
    parser.set_defaults(func=do_add_category)
    parser.add_argument('category_name',
                        help='Name of the new category.')


command_opt = cfg.SubCommandOpt('command',
                                title='Commands',
                                help='Show available commands.',
                                handler=add_command_parsers)


def main():
    CONF.register_cli_opt(command_opt)
    try:
        default_config_files = cfg.find_config_files('murano-api', 'murano')
        CONF(sys.argv[1:], project='murano-api', prog='murano-manage',
             version=muranoapi.__version__,
             default_config_files=default_config_files)
        logging.setup("murano-api")
    except RuntimeError as e:
        LOG.error("failed to initialize murano-manage: %s" % e)
        sys.exit("ERROR: %s" % e)

    try:
        CONF.command.func()
    except Exception as e:
        tb = traceback.format_exc()
        err_msg = "murano-manage command failed: {0}\n{1}".format(e, tb)
        LOG.error(err_msg)
        sys.exit(err_msg)


if __name__ == '__main__':
    main()
