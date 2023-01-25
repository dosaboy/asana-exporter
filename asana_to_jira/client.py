#!/usr/bin/env python3
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


class AsanaExtractor(object):

    def __init__(self, token, workspace, teamname, export_path,
                 projects_include_filter=None,
                 project_exclude_filter=None):
        self._workspace = workspace
        self.teamname = teamname
        self.export_path = export_path
        self.projects_include_filter = projects_include_filter
        self.project_exclude_filter = project_exclude_filter
        self.token = token
        if not os.path.isdir(self.export_path):
            os.makedirs(self.export_path)

    @cached_property
    def client(self):
        if not self.token:
            raise Exception("api oauth token must be provided with --token")

        return asana.Client.access_token(self.token)

    @cached_property
    def workspace(self):
        """
        If workspace name is provided we need to lookup its GID.
        """
        try:
            return int(self._workspace)
        except ValueError:
            print("INFO: resolving workspace gid from name '{}'".
                  format(self._workspace))
            return [w['gid'] for w in self.client.workspaces.find_all()
                    if w['name'] == self._workspace][0]

    @property
    def teams_json(self):
        return os.path.join(self.export_path, 'teams.json')

    def get_teams(self, update_from_api=True):
        teams = []
        if os.path.exists(self.teams_json):
            print("INFO: using cached teams")
            with open(self.teams_json) as fd:
                teams = json.loads(fd.read())
        elif update_from_api:
            print("INFO: fetching teams")
            teams = []
            for p in self.client.teams.find_by_organization(self.workspace):
                teams.append(p)

            with open(self.teams_json, 'w') as fd:
                fd.write(json.dumps(teams))

            print("INFO: saved {} teams".format(len(teams)))

        print("INFO: fetched {} teams".format(len(teams)))
        return teams

    @cached_property
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

    def get_projects(self, update_from_api=True):
        """
        Fetch projects owned by a give team. By default this will get projects
        from the Asana api and add them to the extraction archive. This process
        is additive and can be repeated to update an existing cache/archive but
        is not subtractive so if a project is deleted from Asana it will remain
        in the archive.

        Since the api can be slow, it is recommended to use the available
        project filters.
        """
        projects = []
        print("INFO: fetching projects")
        if not update_from_api:
            if not os.path.exists(self.projects_json):
                print("INFO: no cached projects to list")
                return projects

            with open(self.projects_json) as fd:
                projects = json.loads(fd.read())
        else:
            for p in self.client.projects.find_by_workspace(
                                                      workspace=self.workspace,
                                                      team=self.team['gid']):
                if self.projects_include_filter:
                    if not re.search(self.projects_include_filter, p['name']):
                        print("INFO: ignoring project {}".format(p['name']))
                        continue

                if self.project_exclude_filter:
                    if re.search(self.project_exclude_filter, p['name']):
                        print("INFO: ignoring project {}".format(p['name']))
                        continue

                projects.append(p)

            print("INFO: updating project cache")
            if os.path.exists(self.projects_json):
                with open(self.projects_json) as fd:
                    _projects = json.loads(fd.read())

                for p in _projects:
                    if p not in projects:
                        projects.append(p)

            with open(self.projects_json, 'w') as fd:
                fd.write(json.dumps(projects))

        print("INFO: fetched {} projects for team '{}'".
              format(len(projects), self.team['name']))
        return projects

    def get_project_templates(self):
        root_path = path = os.path.join(self.projects_dir)
        if os.path.exists(os.path.join(root_path, 'templates.json')):
            print("INFO: using cached project templates")
            with open(os.path.join(root_path, 'templates.json')) as fd:
                templates = json.loads(fd.read())
        else:
            print("INFO: fetching project templates for team '{}'".
                  format(self.team['name']))
            path = os.path.join(root_path, 'templates')
            os.makedirs(path)

            templates = []
            for t in self.client.projects.find_by_team(team=self.team['gid'],
                                                       is_template=True):
                templates.append(t)

            with open(os.path.join(root_path, 'templates.json'), 'w') as fd:
                fd.write(json.dumps(templates))

            print("INFO: saved {} templates".format(len(templates)))

        print("INFO: fetched {} templates".format(len(templates)))
        return templates

    @with_lock
    def get_project_tasks(self, project, update_from_api=True):
        tasks = []
        root_path = path = os.path.join(self.projects_dir, project['gid'])
        if os.path.exists(os.path.join(root_path, 'tasks.json')):
            print("INFO: using cached tasks for project '{}'".
                  format(project['name']))
            with open(os.path.join(root_path, 'tasks.json')) as fd:
                tasks = json.loads(fd.read())
        elif update_from_api:
            print("INFO: fetching tasks for project '{}'".
                  format(project['name']))
            path = os.path.join(root_path, 'tasks')
            os.makedirs(path)

            tasks = []
            for t in self.client.projects.tasks(project['gid']):
                tasks.append(t)

            for t in tasks:
                if os.path.exists(os.path.join(path, t['gid'])):
                    continue

                os.makedirs(os.path.join(path, t['gid']))

            with open(os.path.join(root_path, 'tasks.json'), 'w') as fd:
                fd.write(json.dumps(tasks))

            print("INFO: saved {} tasks".format(len(tasks)))

        print("INFO: fetched {} tasks".format(len(tasks)))
        return tasks

    @with_lock
    def get_task_stories(self, project, task):
        root_path = os.path.join(self.projects_dir, project['gid'], 'tasks',
                                 task['gid'])
        p_name = project['name'].strip()
        t_name = task['name'].strip()
        if os.path.exists(os.path.join(root_path, 'stories.json')):
            print("INFO: using cached stories for project '{}' task '{}'".
                  format(p_name, t_name))
            with open(os.path.join(root_path, 'stories.json')) as fd:
                stories = json.loads(fd.read())
        else:
            print("INFO: fetching stories for project='{}' task='{}'".
                  format(p_name, t_name))
            path = os.path.join(root_path, 'stories')
            os.makedirs(path)

            stories = []
            for s in self.client.stories.find_by_task(task['gid']):
                stories.append(s)

            for s in stories:
                with open(os.path.join(path, s['gid']), 'w') as fd:
                    fd.write(json.dumps(s))

            with open(os.path.join(root_path, 'stories.json'),
                      'w') as fd:
                fd.write(json.dumps(stories))

            print("INFO: saved {} stories".format(len(stories)))

        print("INFO: fetched {} stories".format(len(stories)))
        return stories

    def run_job(self, project):
        for t in self.get_project_tasks(project):
            self.get_task_stories(project, t)

    def run(self):
        print("INFO: starting extraction to {}".format(self.export_path_team))
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
        print("INFO: extraction completed in {} secs.".
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
    parser.add_argument('--projects-filter', type=str,
                        default=None,
                        help=("Regular expression filter used to include "
                              "projects."))
    parser.add_argument('--list-teams', action='store_true',
                        default=False,
                        help=("List all cached teams. This will only "
                              "return teams retrieved from an existing "
                              "extraction archive and will not perform any "
                              "api calls."))
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
                        projects_include_filter=args.projects_filter,
                        project_exclude_filter=args.exclude_projects)
    if args.list_teams:
        teams = ae.get_teams(update_from_api=False)
        if teams:
            print("\nTeams:")
            print('\n'.join([t['name'] for t in teams]))
    elif args.list_projects:
        if not all([args.team]):
            msg = ("one or more of the following required options have not "
                   "been provided: --team")
            raise Exception(msg)

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
        if not all([args.token, args.workspace, args.team]):
            msg = ("one or more of the following required options have not "
                   "been provided: --token, --workspace, --team")
            raise Exception(msg)

        ae.run()


if __name__ == "__main__":
    main()
