#!/usr/bin/env python

"""CS290 administrative utility.

Usage:
  cs290 aws TEAM
  cs290 aws-cleanup
  cs290 aws-groups
  cs290 aws-purge TEAM
  cs290 cftemplate [--app-ami=ami] [--multi] [--passenger] [--memcached]
  cs290 gh TEAM USER...

-h --help  show this message
"""

from __future__ import print_function
import copy
from datetime import datetime, timedelta, tzinfo
from docopt import docopt
from pprint import pprint
import json
import os
import random
import string
import sys


class AWS(object):

    """This class handled AWS administrative tasks."""

    EC2_INSTANCES = ['t1.micro', 'm1.small']
    RDB_INSTANCES = ['db.{0}'.format(x) for x in EC2_INSTANCES]
    REGION = 'us-west-2'
    ARNCF = 'arn:aws:cloudformation:{0}:*:{{0}}'.format(REGION)
    ARNEC2 = 'arn:aws:ec2:{0}:*:{{0}}'.format(REGION)
    ARNELB = ('arn:aws:elasticloadbalancing:{0}:*:loadbalancer/{{0}}'
              .format(REGION))
    ARNRDS = 'arn:aws:rds:{0}:*:db:{{0}}'.format(REGION)
    POLICY = {'Statement':
              [{'Action': ['autoscaling:*',  # No fine grained permissions
                           'cloudformation:CreateUploadBucket',
                           'cloudformation:Describe*',
                           'cloudformation:Get*',
                           'cloudformation:ListStack*',
                           'cloudformation:ValidateTemplate',
                           'cloudwatch:DescribeAlarms',
                           'cloudwatch:GetMetricStatistics',
                           'elasticloadbalancing:Describe*', 'rds:Describe*',
                           'rds:ListTagsForResource', 's3:Get*',
                           's3:PutObject', 'sts:DecodeAuthorizationMessage'],
                'Effect': 'Allow', 'Resource': '*'},
               {'Action': ['ec2:Describe*'],
                'Condition': {'StringEquals': {'ec2:Region': REGION}},
                'Effect': 'Allow', 'Resource': '*'}]}
    GROUP = 'cs290'
    PROFILE = 'admin'

    @staticmethod
    def op(serv, operation, debug_output=True, **kwargs):
        """Execute an AWS operation and check the response status."""
        code, data = serv[0].get_operation(operation).call(serv[1], **kwargs)
        if code.status_code == 200:
            if debug_output:
                print('Success: {0} {1}'.format(operation, kwargs))
            return data
        else:
            print(data['Error']['Message'])
            return False

    @staticmethod
    def operation_list(service_name):
        """Output the available API commands and exit."""
        pprint(service_name[0].operations)
        sys.exit(1)

    def __init__(self):
        """Initialize the AWS class."""
        import botocore.session
        self.aws = botocore.session.get_session()
        self.aws.profile = self.PROFILE
        self.ec2 = self.get_service('ec2', self.REGION)
        self.iam = self.get_service('iam', None)

    def cleanup(self):
        """Clean up old stacks and EC2 instances."""
        cf = self.get_service('cloudformation', self.REGION)
        now = datetime.now(UTC())
        for stack in self.op(cf, 'ListStacks', False)['StackSummaries']:
            if stack['StackStatus'] in {'DELETE_COMPLETE'}:
                continue
            if now - stack['CreationTime'] > timedelta(hours=8):
                self.op(cf, 'DeleteStack', StackName=stack['StackName'])

    def configure(self, team):
        """Create account and configure settings for a team.

        This method can be run subsequent times to apply team updates.
        """
        # self.operation_list(self.ec2)
        # self.operation_list(self.iam)

        # Create cs290 group if it does not exist
        self.op(self.iam, 'CreateGroup', GroupName=self.GROUP)
        self.op(self.iam, 'PutGroupPolicy', GroupName=self.GROUP,
                PolicyName=self.GROUP, PolicyDocument=json.dumps(self.POLICY))

        # Configure user account / password / access keys / keypair
        if self.op(self.iam, 'CreateUser', UserName=team):
            self.op(self.iam, 'CreateLoginProfile', UserName=team,
                    Password=generate_password())
            data = self.op(self.iam, 'CreateAccessKey', UserName=team)
            if data:
                print('AccessKey: {0}'
                      .format(data['AccessKey']['AccessKeyId']))
                print('SecretKey: {0}'
                      .format(data['AccessKey']['SecretAccessKey']))
            data = self.op(self.ec2, 'CreateKeyPair', KeyName=team)
            if data:
                filename = '{0}.pem'.format(team)
                with open(filename, 'w') as fd:
                    os.chmod(filename, 0600)
                    fd.write(data['KeyMaterial'])
                print('Keypair saved as: {0}'.format(filename))
        self.op(self.iam, 'AddUserToGroup', GroupName=self.GROUP,
                UserName=team)

        # Configure security group
        self.op(self.ec2, 'CreateSecurityGroup', GroupName=team,
                Description=team)
        for port in [22, 80, 443]:  # Open standard ports to all addresses.
            # These are run one at a time so that existance of one doesn't
            # prevent the creation of the others.
            rule = {'IpProtocol': 'tcp', 'FromPort': port, 'ToPort': port,
                    'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}
            self.op(self.ec2, 'AuthorizeSecurityGroupIngress',
                    GroupName=team, IpPermissions=[rule])
        # Permit all instances in the SecurityGroup to talk to each other
        self.op(self.ec2, 'AuthorizeSecurityGroupIngress', GroupName=team,
                IpPermissions=[
                    {'IpProtocol': '-1', 'FromPort': 0, 'ToPort': 65535,
                     'UserIdGroupPairs': [{'GroupName': team}]}])

        policy = {'Statement': []}
        # State-based policies
        policy['Statement'].append(
            {'Action': ['cloudformation:CreateStack',
                        'cloudformation:DeleteStack',
                        'cloudformation:UpdateStack'],
             'Effect': 'Allow',
             'Resource': AWS.ARNCF.format('stack/{0}*'.format(team))})
        policy['Statement'].append(
            {'Action': ['ec2:RebootInstances', 'ec2:StartInstances',
                        'ec2:StopInstances', 'ec2:TerminateInstances'],
             'Condition': {
                 'StringLike': {
                     'ec2:ResourceTag/aws:cloudformation:stack-name':
                     '{0}*'.format(team)}},
             'Effect': 'Allow', 'Resource': AWS.ARNEC2.format('instance/*')})
        policy['Statement'].append(
            {'Action': 'elasticloadbalancing:*',
             'Effect': 'Allow',
             'Resource': AWS.ARNELB.format('{}*'.format(team))})
        policy['Statement'].append(
            {'Action': ['rds:DeleteDBInstance', 'rds:RebootDBInstance'],
             'Effect': 'Allow',
             'Resource': AWS.ARNRDS.format('{0}*'.format(team))})
        # Creation policies
        policy['Statement'].append(
            {'Action': 'ec2:RunInstances',
             'Effect': 'Allow',
             'Resource': [AWS.ARNEC2.format('image/*'),
                          AWS.ARNEC2.format('key-pair/{0}'.format(team)),
                          AWS.ARNEC2.format('network-interface/*'),
                          AWS.ARNEC2.format('security-group/*'),
                          AWS.ARNEC2.format('subnet/*'),
                          AWS.ARNEC2.format('volume/*')]})
        # Filter the EC2 instances types that are allowed to be started
        policy['Statement'].append(
            {'Action': 'ec2:RunInstances',
             'Condition': {
                 'StringLike': {'ec2:InstanceType': self.EC2_INSTANCES}},
             'Effect': 'Allow',
             'Resource': AWS.ARNEC2.format('instance/*')})
        # Filter the RDS instance types that are allowed to be started
        policy['Statement'].append(
            {'Action': ['rds:CreateDBInstance', 'rds:ModifyDBInstance'],
             'Condition': {
                 'Bool': {'rds:MultiAz': 'false'},
                 'NumericEquals': {'rds:Piops': '0', 'rds:StorageSize': '5'},
                 'StringEquals': {'rds:DatabaseEngine': 'mysql'},
                 'StringLike': {'rds:DatabaseClass': self.RDB_INSTANCES}},
             'Effect': 'Allow',
             'Resource': AWS.ARNRDS.format('{0}*'.format(team))})
        self.op(self.iam, 'PutUserPolicy', UserName=team,
                PolicyName=team, PolicyDocument=json.dumps(policy))

        return 0

    def get_service(self, service_name, endpoint_name):
        """Return a tuple containing the service and associated endpoint."""
        service = self.aws.get_service(service_name)
        return service, service.get_endpoint(endpoint_name)

    def list_security_groups(self):
        """Output the teams and their security groups.

        This function is useful for updating the CFTemplate.TEAM2SG value.
        """
        retval = self.op(self.ec2, 'DescribeSecurityGroups')
        pprint({x['GroupName']: {'sg': x['GroupId']} for x in
                retval['SecurityGroups']})

    def purge(self, team):
        """Remove all settings pertaining to `team`."""
        self.op(self.iam, 'DeleteLoginProfile', UserName=team)
        self.op(self.iam, 'DeleteUserPolicy', UserName=team,
                PolicyName=team)
        resp = self.op(self.iam, 'ListAccessKeys', UserName=team)
        if resp:
            for keydata in resp['AccessKeyMetadata']:
                self.op(self.iam, 'DeleteAccessKey', UserName=team,
                        AccessKeyId=keydata['AccessKeyId'])

        self.op(self.iam, 'RemoveUserFromGroup', GroupName=self.GROUP,
                UserName=team)
        self.op(self.iam, 'DeleteUser', UserName=team)
        self.op(self.ec2, 'DeleteKeyPair', KeyName=team)
        self.op(self.ec2, 'DeleteSecurityGroup', GroupName=team)
        return 0

    def verify_template(self, template):
        """Verify a cloudformation template."""
        cf = self.get_service('cloudformation', self.REGION)
        print(self.op(cf, 'ValidateTemplate', TemplateBody=template))


class CFTemplate(object):

    """Generate CS290 Cloudformation templates."""

    DEFAULT_AMI = 'ami-55a7ea65'
    INSTANCES = ['t1.micro', 'm1.small', 'm1.medium', 'm1.large', 'm1.xlarge',
                 'm2.xlarge', 'm2.2xlarge', 'm2.4xlarge', 'm3.xlarge',
                 'm3.2xlarge']
    # Update this value periodically from the `cs290 aws-groups` output.
    TEAM_MAP = {'BaconWindshield': {'sg': 'sg-ab3052ce'},
                'Compete': {'sg': 'sg-d33052b6'},
                'Gradr': {'sg': 'sg-b53052d0'},
                'LaPlaya': {'sg': 'sg-dd3052b8'},
                'Lab-App': {'sg': 'sg-763c5213'},
                'Motley-Crew': {'sg': 'sg-fa97fa9f'},
                'Suppr': {'sg': 'sg-b13052d4'},
                'Team-Hytta': {'sg': 'sg-1297fa77'},
                'Upvid': {'sg': 'sg-bd3052d8'},
                'Xup': {'sg': 'sg-a03052c5'},
                'labapp': {'sg': 'sg-661f7203'},
                'picShare': {'sg': 'sg-db3052be'}}
    TEMPLATE = {'AWSTemplateFormatVersion': '2010-09-09',
                'Outputs': {},
                'Parameters': {},
                'Resources': {}}

    @staticmethod
    def get_att(resource, attribute):
        """Apply the 'Fn:GetAtt' function on resource for attribute."""
        return {'Fn:GetAtt': [resource, attribute]}

    @staticmethod
    def join(separator, *args):
        """Apply the 'Fn:Join' function to args using separator."""
        return {'Fn:Join': [separator, args]}

    def __init__(self, app_ami, memcached, multi, passenger):
        """Initialize the CFTemplate class.

        :param app_ami: (str) The AMI to use for the app server instance(s).
        :param memcached: (boolean) Template specifies a separate memcached
            instance.
        :param multi: (boolean) Template moves the database to its own RDB
            instance, permits a variable number of app server instances, and
            distributes load to those instances via ELB.
        :param passenger: (boolean) Use passenger standalone (nginx) as the
            entry-point into each app server rather than `rails s` (WEBrick by
            default).
        """
        self.ami = app_ami if app_ami else self.DEFAULT_AMI
        self.memcached = memcached
        self.multi = multi
        self.passenger = passenger
        self.template = copy.deepcopy(self.TEMPLATE)

    def add_output(self, name, description, value):
        """Add a template output value."""
        self.template['Outputs'][name] = {'Description': description,
                                          'Value': value}

    def add_parameter(self, name, ptype='String', allowed=None, default=None,
                      description=None, error_msg=None, maxv=None, minv=None):
        """Add a template parameter."""
        param = {'Type': ptype}
        if allowed:
            param['AllowedValues'] = allowed
        if default:
            param['Default'] = default
        if description:
            param['Description'] = description
        if error_msg:
            param['ConstraintDescription'] = error_msg
        if maxv:
            param['MaxValue'] = maxv
        if minv:
            param['MinValue'] = minv
        self.template['Parameters'][name] = param

    def generate(self):
        """Output the generated AWS cloudformation template."""

        # Common configuration
        self.add_parameter('AppInstanceType', allowed=self.INSTANCES,
                           default='t1.micro',
                           description='The AppServer instance type.',
                           error_msg=('Must be a valid t1, m1, or m2 EC2 '
                                      'instance type.'))
        self.add_parameter('Branch', default='master',
                           description='The git branch to deploy.')
        self.add_parameter('TeamName', allowed=self.TEAM_MAP.keys(),
                           description='Your CS290 team name.',
                           error_msg=('Must exactly match your team name as '
                                      'shown in your Github URL.'))

        if self.multi:
            url = self.get_att('LoadBalancer', 'DNSName')
            self.add_parameter('AppInstances', 'Number', default=2,
                               description=('The number of AppServer instances'
                                            ' to launch.'),
                               maxv=8, minv=1)
            self.add_parameter('DBInstanceType', allowed=['db.' + x for x in
                                                          self.INSTANCES],
                               default='db.t1.micro',
                               description='The Database instance type.',
                               error_msg=('Must be a valid db.t1, db.m1, or '
                                          'db.m2 EC2 instance type.'))
            self.template['Mappings'] = {'Teams': self.TEAM_MAP}
        else:
            url = self.get_att('AppServer', 'PublicDnsName')
        self.add_output('URL', 'The URL to the rails application.',
                        self.join('', 'http://', url))

        template = json.dumps(self.template, indent=4, separators=(',', ': '),
                              sort_keys=True)
        print(template)
        AWS().verify_template(template)


class UTC(tzinfo):

    """Specify the UTC timezone.

    From: http://docs.python.org/release/2.4.2/lib/datetime-tzinfo.html
    """

    dst = lambda x, y: timedelta(0)
    tzname = lambda x, y: 'UTC'
    utcoffset = lambda x, y: timedelta(0)


def configure_github_team(team_name, user_names):
    """Create team and team repository and add users to the team on Github."""
    from github3 import login
    print("""About to create:
     Team: {0}
     Members: {1}\n""".format(team_name, ', '.join(user_names)))
    sys.stdout.write('Do you want to continue? [yN]: ')
    sys.stdout.flush()
    if sys.stdin.readline().strip().lower() not in ['y', 'yes', '1']:
        print('Aborting')
        return 1

    gh_token, _ = get_github_token()
    gh = login(token=gh_token)
    org = gh.membership_in('scalableinternetservices').organization

    team = None  # Fetch or create team
    for iteam in org.iter_teams():
        if iteam.name == team_name:
            team = iteam
            break
    if team is None:
        team = org.create_team(team_name, permission='push')

    repo = None  # Fetch or create repository
    for irepo in org.iter_repos('public'):
        if irepo.name == team_name:
            repo = irepo
            break
    if repo is None:  # Create repo and associate with the team
        repo = org.create_repo(team_name, has_wiki=False,
                               has_downloads=False, team_id=team.id)
    elif team not in list(repo.iter_teams()):
        print(org.add_repo(repo, team))

    # Add PT integration hook
    pt_token = get_pivotaltracker_token()
    if pt_token:
        if not repo.create_hook('pivotaltracker', {'token': pt_token}):
            print('Failed to add PT hook.')

    for user in user_names:  # Add users to the team
        print(team.invite(user))

    return 0


def generate_password(length=16):
    """Generate a random password containing letters and digits."""
    ALPHA = string.ascii_letters + string.digits
    return ''.join(random.choice(ALPHA) for _ in range(length))


def get_github_token():
    """Fetch and/or load API authorization token for Github."""
    credential_file = os.path.expanduser('~/.config/github_creds')
    if os.path.isfile(credential_file):
        with open(credential_file) as fd:
            token = fd.readline().strip()
            auth_id = fd.readline().strip()
            return token, auth_id

    from github3 import authorize
    from getpass import getuser, getpass

    def two_factor_callback():
        sys.stdout.write('Two factor token: ')
        sys.stdout.flush()
        return sys.stdin.readline().strip()

    user = getuser()
    auth = authorize(user, getpass('Password for {0}: '.format(user)),
                     ['public_repo'], 'CS290 Create Repo Script',
                     'http://example.com',
                     two_factor_callback=two_factor_callback)

    with open(credential_file, 'w') as fd:
        fd.write('{0}\n{1}\n'.format(auth.token, auth.id))
    return auth.token, auth.id


def get_pivotaltracker_token():
    """Return PivotalTracker API token if it exists."""
    token_file = os.path.expanduser('~/.config/pivotaltracker_token')
    if os.path.isfile(token_file):
        with open(token_file) as fd:
            token = fd.readline().strip()
    else:
        from getpass import getpass
        token = getpass('PivotalTracker API token: ').strip()
        if token:
            with open(token_file, 'w') as fd:
                fd.write('{0}\n'.format(token))
    return token if token else None


def main():
    """Enter cs290.py."""
    args = docopt(__doc__)

    if args['TEAM']:
        args['TEAM'] = args['TEAM'].replace(' ', '-')

    if args['aws']:
        return AWS().configure(args['TEAM'])
    elif args['aws-cleanup']:
        return AWS().cleanup()
    elif args['aws-groups']:
        return AWS().list_security_groups()
    elif args['aws-purge']:
        return AWS().purge(args['TEAM'])
    elif args['cftemplate']:
        return CFTemplate(app_ami=args['--app-ami'],
                          memcached=args['--memcached'], multi=args['--multi'],
                          passenger=args['--passenger']).generate()
    elif args['gh']:
        return configure_github_team(team_name=args['TEAM'],
                                     user_names=args['USER'])
    else:
        raise Exception('Invalid state')


if __name__ == '__main__':
    sys.exit(main())
