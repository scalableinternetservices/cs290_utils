"""Scalable Internet Services administrative utility.

Usage:
  scalable_admin aws TEAM...
  scalable_admin aws-cleanup
  scalable_admin aws-purge TEAM...
  scalable_admin aws-update-all
  scalable_admin cftemplate [--no-test] [--multi] [--memcached] [--puma]
  scalable_admin cftemplate tsung [--no-test]
  scalable_admin cftemplate-update-all [--no-test]
  scalable_admin gh TEAM USER...

-h --help  show this message
"""
from __future__ import print_function
from docopt import docopt
from . import AWS, CFTemplate
from .github import configure_github_team
from .helper import parse_config


def clean_team_names(args):
    """Replace spaces with hyphens in team names."""
    if args['TEAM']:
        if isinstance(args['TEAM'], list):
            for i, item in enumerate(args['TEAM']):
                args['TEAM'][i] = item.strip().replace(' ', '-')
        else:
            args['TEAM'] = args['TEAM'].strip().replace(' ', '-')


def cmd_aws(args):
    """Handle the aws command."""
    for team in args['TEAM']:
        retval = AWS().configure(team)
        if retval:
            return retval
    return 0


def cmd_aws_cleanup(_):
    """Handle the aws-cleanup command."""
    return AWS().cleanup()


def cmd_aws_purge(args):
    """Handle the aws-purge command."""
    for team in args['TEAM']:
        retval = AWS().purge(team)
        if retval:
            return retval
    return 0


def cmd_aws_update_all(args):
    """Handle the aws-update-all command."""
    aws = AWS()
    for team in aws.team_to_security_group():
        retval = aws.configure(team)
        if retval:
            return retval
    return 0


def cmd_cftemplate(args):
    """Handle the cftemplate command."""
    cf = CFTemplate(test=not args['--no-test'])
    if args['tsung']:
        return cf.generate_tsung()
    return cf.generate_stack(app_ami=None,
                             memcached=args['--memcached'],
                             multi=args['--multi'], puma=args['--puma'])


def cmd_cftemplate_update_all(args):
    """Handle the cftemplate-update-all command."""
    bit_pos = ['memcached', 'puma', 'multi']
    for i in range(2 ** len(bit_pos)):
        kwargs = {'app_ami': None}
        for bit, argument in enumerate(bit_pos):
            kwargs[argument] = bool(i & 2 ** bit)
        cf = CFTemplate(test=not args['--no-test'])
        retval = cf.generate_stack(**kwargs)
        if retval:
            return retval
    return 0


def cmd_gh(args):
    """Handle the gh command."""
    team = args['TEAM']
    team = team[0] if isinstance(team, list) else team
    return configure_github_team(team_name=team, user_names=args['USER'])


def main():
    """Provide the entrance point for the scalable_admin command."""
    args = docopt(__doc__)

    parse_config(AWS)
    clean_team_names(args)

    commands = {'aws': cmd_aws,
                'aws-cleanup': cmd_aws_cleanup,
                'aws-purge': cmd_aws_purge,
                'aws-update-all': cmd_aws_update_all,
                'cftemplate': cmd_cftemplate,
                'cftemplate-update-all': cmd_cftemplate_update_all,
                'gh': cmd_gh}

    for command_name in commands:
        if args[command_name]:
            return commands[command_name](args)
    else:
        raise Exception('Invalid state')
