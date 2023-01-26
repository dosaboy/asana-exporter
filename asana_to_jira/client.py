#!/usr/bin/env python3
import abc
import argparse
import os
import re
import time
import threading

import asana
import json

from functools import cached_property

LOCK = threading.Lock()


def with_lock(f):
    def with_lock_inner(*args, **kwargs):
        with LOCK:
            return f(*args, **kwargs)

    return with_lock_inner


def required(opts):
    """
    @param opts: dict where key is attr name and val is opt name.
    """
    def _required(f):
        def _inner_required(self, *args, **kwargs):
            has = all([hasattr(self, o) for o in opts])
            if not has or not all([getattr(self, o) for o in opts]):
                msg = ("one or more of the following required options have "
                       "not been provided: {}".
                       format(', '.join(opts.values())))
                raise Exception(msg)
            return f(self, *args, **kwargs)

        return _inner_required
    return _required


class Logger(object):

    def debug(self, msg):
        print("DEBUG: {}".format(msg))

    def info(self, msg):
        print("INFO: {}".format(msg))

    def warning(self, msg):
        print("WARNING: {}".format(msg))

    def error(self, msg):
        print("ERROR: {}".format(msg))


LOG = Logger()


class AsanaResourceBase(abc.ABC):

    @abc.abstractmethod
    def _from_api(self):
        """ Fetch resources from the API. """

    @abc.abstractmethod
    def _from_local(self):
        """ Fetch resources from the local cache/export. """

    @with_lock
    def get(self, update_from_api=True, prefer_cache=True):
        if prefer_cache:
            objs = self._from_local()
        else:
            objs = []

        if not objs:
            if update_from_api:
                objs = self._from_api()

        if not objs and not prefer_cache:
            objs = self._from_local()

        return objs


class AsanaProjects(AsanaResourceBase):

    def __init__(self, client, workspace, team,
                 project_include_filter, project_exclude_filter,
                 projects_json):
        self.client = client
        self.workspace = workspace
        self.project_include_filter = project_include_filter
        self.project_exclude_filter = project_exclude_filter
        self.projects_json = projects_json
        self.team = team

    def _from_local(self):
        projects = []
        LOG.info("fetching projects for team '{}' from cache".
                 format(self.team['name']))
        if not os.path.exists(self.projects_json):
            LOG.info("no cached projects to list")
            return projects

        with open(self.projects_json) as fd:
            projects = json.loads(fd.read())

        return projects

    def _from_api(self):
        LOG.info("fetching projects for team '{}' from api".
                 format(self.team['name']))
        total = 0
        ignored = []
        projects = []
        for p in self.client.projects.find_by_workspace(
                                                  workspace=self.workspace,
                                                  team=self.team['gid']):
            total += 1
            if self.project_include_filter:
                if not re.search(self.project_include_filter, p['name']):
                    ignored.append(p['name'])
                    continue

            if self.project_exclude_filter:
                if re.search(self.project_exclude_filter, p['name']):
                    ignored.append(p['name'])
                    continue

            projects.append(p)

        if ignored:
            LOG.info("ignoring projects:\n  {}".
                     format('\n  '.join(ignored)))

        LOG.info("saving {}/{} projects".format(len(projects), total))
        if projects:
            LOG.info("updating project cache")
            """
            if os.path.exists(self.projects_json):
                with open(self.projects_json) as fd:
                    _projects = json.loads(fd.read())

                for p in _projects:
                    if p not in projects:
                        projects.append(p)
            """

            with open(self.projects_json, 'w') as fd:
                fd.write(json.dumps(projects))

        LOG.info("fetched {} projects for team '{}'".
                 format(len(projects), self.team['name']))
        return projects


class AsanaProjectTasks(AsanaResourceBase):

    def __init__(self, client, project, projects_dir):
        self.client = client
        self.project = project
        self.root_path = os.path.join(projects_dir, project['gid'])

    def _from_local(self):
        tasks = []
        if not os.path.exists(os.path.join(self.root_path, 'tasks.json')):
            return tasks

        LOG.info("fetching tasks for project '{}' (gid={}) from cache".
                 format(self.project['name'], self.project['gid']))
        with open(os.path.join(self.root_path, 'tasks.json')) as fd:
            tasks = json.loads(fd.read())

        LOG.info("fetched {} tasks".format(len(tasks)))
        return tasks

    def _from_api(self):
        tasks = []
        LOG.info("fetching tasks for project '{}' (gid={}) from api".
                 format(self.project['name'], self.project['gid']))
        path = os.path.join(self.root_path, 'tasks')
        if not os.path.isdir(path):
            os.makedirs(path)

        tasks = []
        for t in self.client.projects.tasks(self.project['gid']):
            tasks.append(t)

        for t in tasks:
            task_path = os.path.join(path, t['gid'])
            if os.path.exists(task_path):
                continue

            if not os.path.isdir(task_path):
                os.makedirs(task_path)

        with open(os.path.join(self.root_path, 'tasks.json'), 'w') as fd:
            fd.write(json.dumps(tasks))

        LOG.info("saved {} tasks".format(len(tasks)))
        LOG.info("fetched {} tasks".format(len(tasks)))
        return tasks


class AsanaProjectTaskStories(AsanaResourceBase):

    def __init__(self, client, project, projects_dir, task):
        self.client = client
        self.project = project
        self.task = task
        self.root_path = os.path.join(projects_dir, project['gid'],
                                      'tasks', task['gid'])

    def _from_local(self):
        stories = []
        p_gid = self.project['gid'].strip()
        t_name = self.task['name'].strip()
        if os.path.exists(os.path.join(self.root_path, 'stories.json')):
            LOG.info("fetching stories for task '{}' (project={}) from cache".
                     format(t_name, p_gid))
            with open(os.path.join(self.root_path, 'stories.json')) as fd:
                stories = json.loads(fd.read())

        return stories

    def _from_api(self):
        p_gid = self.project['gid'].strip()
        t_name = self.task['name'].strip()
        LOG.info("fetching stories for task='{}' (project gid={}) from "
                 "api".format(t_name, p_gid))
        path = os.path.join(self.root_path, 'stories')
        if not os.path.isdir(path):
            os.makedirs(path)

        stories = []
        for s in self.client.stories.find_by_task(self.task['gid']):
            stories.append(s)

        for s in stories:
            with open(os.path.join(path, s['gid']), 'w') as fd:
                fd.write(json.dumps(s))

        with open(os.path.join(self.root_path, 'stories.json'),
                  'w') as fd:
            fd.write(json.dumps(stories))

        LOG.info("saved {} stories".format(len(stories)))
        return stories


class AsanaExtractor(object):

    def __init__(self, token, workspace, teamname, export_path,
                 project_include_filter=None,
                 project_exclude_filter=None):
        self._workspace = workspace
        self.teamname = teamname
        self.export_path = export_path
        self.project_include_filter = project_include_filter
        self.project_exclude_filter = project_exclude_filter
        self.token = token
        if not os.path.isdir(self.export_path):
            os.makedirs(self.export_path)

    @cached_property
    @required({'token': '--token'})
    def client(self):
        return asana.Client.access_token(self.token)

    @cached_property
    def workspace(self):
        """
        If workspace name is provided we need to lookup its GID.
        """
        try:
            return int(self._workspace)
        except ValueError:
            LOG.info("resolving workspace gid from name '{}'".
                     format(self._workspace))
            return [w['gid'] for w in self.client.workspaces.find_all()
                    if w['name'] == self._workspace][0]

    @property
    def teams_json(self):
        return os.path.join(self.export_path, 'teams.json')

    def get_teams(self):
        teams = []
        if os.path.exists(self.teams_json):
            LOG.info("using cached teams")
            with open(self.teams_json) as fd:
                teams = json.loads(fd.read())
        else:
            LOG.info("fetching teams from api")
            teams = []
            for p in self.client.teams.find_by_organization(self.workspace):
                teams.append(p)

            with open(self.teams_json, 'w') as fd:
                fd.write(json.dumps(teams))

            LOG.info("saved {} teams".format(len(teams)))

        LOG.info("fetched {} teams".format(len(teams)))
        return teams

    @cached_property
    @required({'token': '--token', '_workspace': '--workspace',
               'teamname': '--team'})
    def team(self):
        for t in self.get_teams():
            if t['name'] == self.teamname:
                return t

        raise Exception("No team found with name '{}'".format(self.teamname))

    @property
    def export_path_team(self):
        return os.path.join(self.export_path, 'teams', self.team['gid'])

    @property
    def projects_json(self):
        return os.path.join(self.export_path_team, 'projects.json')

    @property
    def projects_dir(self):
        return os.path.join(self.export_path_team, 'projects')

    @property
    def tasks_dir(self):
        return os.path.join(self.export_path_team, 'tasks')

    @property
    def stories_dir(self):
        return os.path.join(self.export_path_team, 'stories')

    @required({'teamname': '--team'})
    def get_projects(self, update_from_api=True):
        """
        Fetch projects owned by a give team. By default this will get projects
        from the Asana api and add them to the extraction archive.

        Since the api can be slow, it is recommended to use the available
        project filters.
        """
        return AsanaProjects(self.client, self.workspace, self.team,
                             self.project_include_filter,
                             self.project_exclude_filter,
                             self.projects_json).get(update_from_api,
                                                     prefer_cache=False)

    def get_project_templates(self):
        root_path = path = os.path.join(self.projects_dir)
        if os.path.exists(os.path.join(root_path, 'templates.json')):
            LOG.info("using cached project templates")
            with open(os.path.join(root_path, 'templates.json')) as fd:
                templates = json.loads(fd.read())
        else:
            LOG.info("fetching project templates for team '{}' from api".
                     format(self.team['name']))
            path = os.path.join(root_path, 'templates')
            if not os.path.isdir(path):
                os.makedirs(path)

            templates = []
            for t in self.client.projects.find_by_team(team=self.team['gid'],
                                                       is_template=True):
                templates.append(t)

            with open(os.path.join(root_path, 'templates.json'), 'w') as fd:
                fd.write(json.dumps(templates))

        LOG.info("fetched {} templates".format(len(templates)))
        return templates

    def get_project_tasks(self, project, update_from_api=True):
        """
        @param update_from_api: allow fetching from the API.
        """
        return AsanaProjectTasks(self.client, project,
                                 self.projects_dir).get(update_from_api)

    def get_task_stories(self, project, task, update_from_api=True):
        """
        @param update_from_api: allow fetching from the API.
        """
        return AsanaProjectTaskStories(self.client, project, self.projects_dir,
                                       task).get(update_from_api)

    def run_job(self, project):
        for t in self.get_project_tasks(project):
            self.get_task_stories(project, t)

    def run(self):
        LOG.info("starting extraction to {}".format(self.export_path_team))
        start = time.time()

        if not os.path.isdir(self.export_path_team):
            os.makedirs(self.export_path_team)

        self.get_project_templates()
        threads = []
        for p in self.get_projects():
            t = threading.Thread(target=self.run_job, args=[p])
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        end = time.time()
        LOG.info("extraction completed in {} secs.".
                 format(round(end - start, 3)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', type=str,
                        default=None, help="Asana api token")
    parser.add_argument('--workspace', type=str,
                        default=None, help=("Asana workspace ID or name. If a "
                                            "name is provided an api lookup "
                                            "is done to resolve the ID."))
    parser.add_argument('--team', type=str,
                        default=None, help="Asana team name")
    parser.add_argument('--export-path', type=str,
                        default='asana_export', required=False,
                        help="Path where data is saved.")
    parser.add_argument('--exclude-projects', type=str,
                        default=None,
                        help=("Regular expression filter used to exclude "
                              "projects."))
    parser.add_argument('--project-filter', type=str,
                        default=None,
                        help=("Regular expression filter used to include "
                              "projects."))
    parser.add_argument('--list-teams', action='store_true',
                        default=False,
                        help=("List all teams. This will "
                              "return teams retrieved from an existing "
                              "extraction archive and if not available will "
                              "query the api."))
    parser.add_argument('--list-projects', action='store_true',
                        default=False,
                        help=("List all cached projects. This will only "
                              "return projects retrieved from an existing "
                              "extraction archive and will not perform any "
                              "api calls."))
    parser.add_argument('--list-project-tasks', type=str,
                        default=None,
                        help=("List all cached tasks for a given project. "
                              "This will only "
                              "return project tasks retrieved from an "
                              "existing "
                              "extraction archive and will not perform any "
                              "api calls."))

    args = parser.parse_args()
    ae = AsanaExtractor(token=args.token, workspace=args.workspace,
                        teamname=args.team, export_path=args.export_path,
                        project_include_filter=args.project_filter,
                        project_exclude_filter=args.exclude_projects)
    if args.list_teams:
        teams = ae.get_teams()
        if teams:
            print("\nTeams:")
            print('\n'.join([t['name'] for t in teams]))
    elif args.list_projects:
        projects = ae.get_projects(update_from_api=False)
        if projects:
            print("\nProjects:")
            print('\n'.join([p['name'] for p in projects]))
    elif args.list_project_tasks:
        if not all([args.team]):
            msg = ("one or more of the following required options have not "
                   "been provided: --team")
            raise Exception(msg)
        for p in ae.get_projects(update_from_api=False):
            if p['name'] == args.list_project_tasks:
                tasks = ae.get_project_tasks(p, update_from_api=False)
                if tasks:
                    print("\nTasks:")
                    print('\n'.join([t['name'] for t in tasks]))

                break
    else:
        ae.run()


if __name__ == "__main__":
    main()
