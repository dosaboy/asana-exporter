#!/usr/bin/env python3
import abc
import argparse
import collections
import os
import queue
import re
import time
import threading

import asana
import json
import urllib
import urllib.request

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

    def __init__(self):
        self._debug = False

    def debug(self, msg):
        if self._debug:
            print("DEBUG: {}".format(msg))

    def info(self, msg):
        print("INFO: {}".format(msg))

    def warning(self, msg):
        print("WARNING: {}".format(msg))

    def error(self, msg):
        print("ERROR: {}".format(msg))

    def set_level(self, level):
        if level == 'debug':
            self._debug = True
        else:
            self._debug = False


LOG = Logger()


class AsanaResourceBase(abc.ABC):

    @abc.abstractproperty
    def _local_store(self):
        """
        Path to local store of information. If this exists it suggests we have
        done at least one api request for this resources.
        """

    @abc.abstractproperty
    def stats(self):
        """ Return ExtractorStats object. """

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

        if not objs and not os.path.exists(self._local_store):
            if update_from_api:
                objs = self._from_api()

        if not objs and not prefer_cache:
            objs = self._from_local()

        return objs


class ExtractorStats(collections.UserDict):
    def __init__(self):
        self.data = {}

    def combine(self, stats):
        for key, value in stats.items():
            if key in self.data:
                if type(value) == dict:
                    self.data[key].update(value)
                else:
                    self.data[key] += value
            else:
                self.data[key] = value

    def __repr__(self):
        msg = []
        for key, value in self.items():
            msg.append("{}={}".format(key, value))

        return ', '.join(msg)


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
        self._stats = ExtractorStats()

    @property
    def stats(self):
        return self._stats

    @property
    def _local_store(self):
        return self.projects_json

    def _from_local(self):
        projects = []
        LOG.debug("fetching projects for team '{}' from cache".
                  format(self.team['name']))
        if not os.path.exists(self._local_store):
            LOG.debug("no cached projects to list")
            return projects

        with open(self._local_store) as fd:
            projects = json.loads(fd.read())

        self.stats['num_projects'] = len(projects)
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

        # yield
        time.sleep(0)
        if ignored:
            LOG.info("ignoring projects:\n  {}".
                     format('\n  '.join(ignored)))
            self.stats['num_projects_ignored'] = len(ignored)

        LOG.debug("saving {}/{} projects".format(len(projects), total))
        if projects:
            LOG.debug("updating project cache")
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

        self.stats['num_projects'] = len(projects)
        return projects


class AsanaProjectTasks(AsanaResourceBase):

    def __init__(self, client, project, projects_dir):
        self.client = client
        self.project = project
        self.root_path = os.path.join(projects_dir, project['gid'])
        self._stats = ExtractorStats()

    @property
    def stats(self):
        return self._stats

    @property
    def _local_store(self):
        return os.path.join(self.root_path, 'tasks.json')

    def _from_local(self):
        tasks = []
        if not os.path.exists(self._local_store):
            return tasks

        LOG.debug("fetching tasks for project '{}' (gid={}) from cache".
                  format(self.project['name'], self.project['gid']))
        with open(self._local_store) as fd:
            tasks = json.loads(fd.read())

        LOG.debug("fetched {} tasks".format(len(tasks)))
        self.stats['num_tasks'] = len(tasks)
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

        # yield
        time.sleep(0)
        for t in tasks:
            task_path = os.path.join(path, t['gid'])
            if os.path.exists(task_path):
                continue

            if not os.path.isdir(task_path):
                os.makedirs(task_path)

        with open(os.path.join(self.root_path, 'tasks.json'), 'w') as fd:
            fd.write(json.dumps(tasks))

        self.stats['num_tasks'] = len(tasks)
        return tasks


class AsanaTaskSubTasks(AsanaResourceBase):

    def __init__(self, client, project, projects_dir, task):
        self.client = client
        self.project = project
        self.task = task
        self.root_path = os.path.join(projects_dir, project['gid'],
                                      'tasks', task['gid'])
        self._stats = ExtractorStats()

    @property
    def stats(self):
        return self._stats

    @property
    def _local_store(self):
        return os.path.join(self.root_path, 'subtasks.json')

    def _from_local(self):
        subtasks = []
        p_gid = self.project['gid'].strip()
        t_name = self.task['name'].strip()
        if os.path.exists(self._local_store):
            LOG.debug("fetching subtasks for task '{}' (project={}) from "
                      "cache".format(t_name, p_gid))
            with open(os.path.join(self.root_path, 'subtasks.json')) as fd:
                subtasks = json.loads(fd.read())

        self.stats['num_subtasks'] = len(subtasks)
        return subtasks

    def _from_api(self):
        p_gid = self.project['gid'].strip()
        t_name = self.task['name'].strip()
        LOG.info("fetching subtasks for task='{}' (project gid={}) from "
                 "api".format(t_name, p_gid))
        path = os.path.join(self.root_path, 'subtasks')
        if not os.path.isdir(path):
            os.makedirs(path)

        subtasks = []
        for s in self.client.tasks.subtasks(self.task['gid']):
            subtasks.append(s)

        # yield
        time.sleep(0)
        for t in subtasks:
            task_path = os.path.join(path, t['gid'])
            if os.path.exists(task_path):
                continue

            if not os.path.isdir(task_path):
                os.makedirs(task_path)

        with open(os.path.join(self.root_path, 'subtasks.json'),
                  'w') as fd:
            fd.write(json.dumps(subtasks))

        self.stats['num_subtasks'] = len(subtasks)
        return subtasks


class AsanaProjectTaskStories(AsanaResourceBase):

    def __init__(self, client, project, projects_dir, task):
        self.client = client
        self.project = project
        self.task = task
        self.root_path = os.path.join(projects_dir, project['gid'],
                                      'tasks', task['gid'])
        self._stats = ExtractorStats()

    @property
    def stats(self):
        return self._stats

    @property
    def _local_store(self):
        return os.path.join(self.root_path, 'stories.json')

    def _from_local(self):
        stories = []
        p_gid = self.project['gid'].strip()
        t_name = self.task['name'].strip()
        if os.path.exists(self._local_store):
            LOG.debug("fetching stories for task '{}' (project={}) from cache".
                      format(t_name, p_gid))
            with open(os.path.join(self.root_path, 'stories.json')) as fd:
                stories = json.loads(fd.read())

        self.stats['num_stories'] = len(stories)
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

        # yield
        time.sleep(0)
        for s in stories:
            with open(os.path.join(path, s['gid']), 'w') as fd:
                fd.write(json.dumps(s))

        with open(os.path.join(self.root_path, 'stories.json'),
                  'w') as fd:
            fd.write(json.dumps(stories))

        self.stats['num_stories'] = len(stories)
        return stories


class AsanaProjectTaskAttachments(AsanaResourceBase):

    def __init__(self, client, project, projects_dir, task, subtask=None):
        self.client = client
        self.project = project
        self._task = task
        self._subtask = subtask
        self.root_path = os.path.join(projects_dir, project['gid'],
                                      'tasks', task['gid'])
        if subtask:
            self.root_path = os.path.join(self.root_path, 'subtasks',
                                          subtask['gid'])
        self._stats = ExtractorStats()

    @property
    def stats(self):
        return self._stats

    @property
    def task(self):
        if self._subtask:
            return self._subtask

        return self._task

    @property
    def _local_store(self):
        return os.path.join(self.root_path, 'attachments.json')

    def _download(self, url, path):
        LOG.debug("downloading {} to {}".format(url, path))
        rq = urllib.request.Request(url=url)
        with urllib.request.urlopen(rq) as url_fd:
            with open(path, 'wb') as fd:
                out = 'SOF'
                while out:
                    out = url_fd.read(4096)
                    fd.write(out)

    def _from_local(self):
        attachments = []
        p_gid = self.project['gid'].strip()
        t_name = self.task['name'].strip()
        t_type = ""
        if self._subtask:
            t_type = "sub"

        if os.path.exists(self._local_store):
            LOG.debug("fetching attachments for {}task '{}' (project={}) from "
                      "cache".format(t_type, t_name, p_gid))
            with open(self._local_store) as fd:
                attachments = json.loads(fd.read())

        self.stats['num_attachments'] = len(attachments)
        return attachments

    def _from_api(self):
        p_gid = self.project['gid'].strip()
        t_name = self.task['name'].strip()
        t_type = ""
        if self._subtask:
            t_type = "sub"

        LOG.info("fetching attachments for {}task='{}' (project gid={}) from "
                 "api".format(t_type, t_name, p_gid))
        path = os.path.join(self.root_path, 'attachments')
        if not os.path.isdir(path):
            os.makedirs(path)

        attachments = []
        for s in self.client.attachments.find_by_task(self.task['gid']):
            attachments.append(s)

        # yield
        time.sleep(0)
        for s in attachments:
            # convert "compact" record to "full"
            with open(os.path.join(path, s['gid']), 'w') as fd:
                s = self.client.attachments.find_by_id(s['gid'])
                fd.write(json.dumps(s))
                url = s['download_url']
                if url:
                    self._download(url,
                                   os.path.join(path,
                                                "{}_{}".format(s['gid'],
                                                               'download')))

        with open(os.path.join(self.root_path, 'attachments.json'),
                  'w') as fd:
            fd.write(json.dumps(attachments))

        self.stats['num_attachments'] = len(attachments)
        return attachments


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
    @required({'_workspace': '--workspace'})
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
        stats = ExtractorStats()
        teams = []
        if os.path.exists(self.teams_json):
            LOG.debug("using cached teams")
            with open(self.teams_json) as fd:
                teams = json.loads(fd.read())
        else:
            LOG.debug("fetching teams from api")
            teams = []
            for p in self.client.teams.find_by_organization(self.workspace):
                teams.append(p)

            with open(self.teams_json, 'w') as fd:
                fd.write(json.dumps(teams))

            LOG.debug("saved {} teams".format(len(teams)))

        stats['num_teams'] = len(teams)
        return teams, stats

    @cached_property
    @required({'token': '--token', '_workspace': '--workspace',
               'teamname': '--team'})
    def team(self):
        teams, _ = self.get_teams()
        for t in teams:
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

    @required({'teamname': '--team'})
    def get_projects(self, update_from_api=True):
        """
        Fetch projects owned by a give team. By default this will get projects
        from the Asana api and add them to the extraction archive.

        Since the api can be slow, it is recommended to use the available
        project filters.
        """
        ap = AsanaProjects(self.client, self.workspace, self.team,
                           self.project_include_filter,
                           self.project_exclude_filter,
                           self.projects_json)
        projects = ap.get(update_from_api, prefer_cache=False)
        return projects, ap.stats

    def get_project_templates(self):
        root_path = path = os.path.join(self.projects_dir)
        if os.path.exists(os.path.join(root_path, 'templates.json')):
            LOG.debug("using cached project templates")
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

            # yield
            time.sleep(0)

            with open(os.path.join(root_path, 'templates.json'), 'w') as fd:
                fd.write(json.dumps(templates))

        return templates, {}

    def get_project_tasks(self, project, update_from_api=True):
        """
        @param update_from_api: allow fetching from the API.
        """
        apt = AsanaProjectTasks(self.client, project, self.projects_dir)
        tasks = apt.get(update_from_api)
        return tasks, apt.stats

    def get_task_subtasks(self, project, task, event, stats_queue,
                          update_from_api=True):
        """
        @param update_from_api: allow fetching from the API.
        """
        event.clear()
        atst = AsanaTaskSubTasks(self.client, project, self.projects_dir,
                                 task)
        atst.get(update_from_api)
        # notify any waiting threads that subtasks are now available.
        event.set()
        stats_queue.put(atst.stats)

    def get_task_stories(self, project, task, stats_queue,
                         update_from_api=True):
        """
        @param update_from_api: allow fetching from the API.
        """
        ats = AsanaProjectTaskStories(self.client, project, self.projects_dir,
                                      task)
        ats.get(update_from_api)
        stats_queue.put(ats.stats)

    def get_task_attachments(self, project, task, stats_queue,
                             update_from_api=True):
        """
        @param update_from_api: allow fetching from the API.
        """
        ata = AsanaProjectTaskAttachments(self.client, project,
                                          self.projects_dir, task)
        ata.get(update_from_api)
        stats_queue.put(ata.stats)

    def get_subtask_attachments(self, project, task, event, stats_queue,
                                update_from_api=True):
        """
        @param update_from_api: allow fetching from the API.
        """
        while not event.is_set():
            time.sleep(1)

        st_obj = AsanaTaskSubTasks(self.client, project, self.projects_dir,
                                   task)
        subtasks = st_obj.get()
        stats = st_obj.stats
        stats['num_subtask_attachments'] = 0
        if not subtasks:
            LOG.debug("no subtasks to fetch attachments for")
            return

        attachments = []
        LOG.debug("fetching attachments for {} subtasks".format(len(subtasks)))
        for subtask in subtasks:
            asta = AsanaProjectTaskAttachments(self.client, project,
                                               self.projects_dir, task,
                                               subtask)
            attachments.extend(asta.get(update_from_api))
            stats['num_subtask_attachments'] += asta.stats['num_attachments']

        stats_queue.put(stats)

    def run(self):
        LOG.info("=" * 80)
        LOG.info("starting extraction to {}".format(self.export_path))
        start = time.time()

        if not os.path.isdir(self.export_path_team):
            os.makedirs(self.export_path_team)

        self.get_project_templates()
        threads = []
        projects, stats = self.get_projects()
        stats_queue = queue.Queue()
        for p in projects:
            LOG.info("extracting project {}".format(p['name']))
            tasks, task_stats = self.get_project_tasks(p)
            stats.combine(task_stats)
            for t in tasks:
                thread = threading.Thread(target=self.get_task_stories,
                                          args=[p, t, stats_queue])
                thread.start()
                threads.append(thread)

                event = threading.Event()
                thread = threading.Thread(target=self.get_task_subtasks,
                                          args=[p, t, event, stats_queue])
                thread.start()
                threads.append(thread)

                thread = threading.Thread(target=self.get_task_attachments,
                                          args=[p, t, stats_queue])
                thread.start()
                threads.append(thread)

                thread = threading.Thread(target=self.get_subtask_attachments,
                                          args=[p, t, event, stats_queue])
                thread.start()
                threads.append(thread)

        for t in threads:
            t.join()

        LOG.info("project extraction complete.")
        while not stats_queue.empty():
            _stats = stats_queue.get()
            stats.combine(_stats)

        LOG.info("stats: {}".format(stats))
        end = time.time()
        LOG.info("extraction to {} completed in {} secs.".
                 format(self.export_path, round(end - start, 3)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', default=False,
                        help=("enable debug logging"))
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
    if args.debug:
        LOG.set_level('debug')

    ae = AsanaExtractor(token=args.token, workspace=args.workspace,
                        teamname=args.team, export_path=args.export_path,
                        project_include_filter=args.project_filter,
                        project_exclude_filter=args.exclude_projects)
    if args.list_teams:
        teams, stats = ae.get_teams()
        LOG.debug("stats: {}".format(stats))
        if teams:
            print("\nTeams:")
            print('\n'.join(["{}: '{}'".
                             format(t['gid'], t['name'])
                             for t in teams]))
    elif args.list_projects:
        projects, stats = ae.get_projects(update_from_api=False)
        LOG.debug("stats: {}".format(stats))
        if projects:
            print("\nProjects:")
            print('\n'.join(["{}: '{}'".
                             format(p['gid'], p['name'])
                             for p in projects]))
    elif args.list_project_tasks:
        projects, stats = ae.get_projects(update_from_api=False)
        LOG.debug("stats: {}".format(stats))
        for p in projects:
            if p['name'] == args.list_project_tasks:
                tasks, _ = ae.get_project_tasks(p, update_from_api=False)
                if tasks:
                    print("\nTasks:")
                    print('\n'.join(["{}: '{}'".
                                     format(t['gid'], t['name'])
                                     for t in tasks]))

                break
        else:
            pnames = ["'{}'".format(p['name']) for p in projects]
            LOG.debug("project name '{}' does not match any of the following:"
                      "\n{}".format(args.list_project_tasks,
                                    '\n'.join(pnames)))
    else:
        ae.run()


if __name__ == "__main__":
    main()
