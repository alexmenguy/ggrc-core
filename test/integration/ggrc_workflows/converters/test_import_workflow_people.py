# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""Tests for workflow people imports."""
# pylint: disable=invalid-name

import collections
import string
import ddt

from ggrc.access_control.list import AccessControlList
from ggrc_workflows.models.workflow import Workflow
from ggrc_workflows.models.task_group import TaskGroup
from integration.ggrc import TestCase
from integration.ggrc.models import factories as ggrc_factories


@ddt.ddt
class TestWorkflowPeopleImport(TestCase):
  """Tests for Workflow ACL import."""

  def setUp(self):
    with ggrc_factories.single_commit():
      self.user_emails = [
          ggrc_factories.PersonFactory().email for _ in xrange(8)]
    self.wf_slug = self.tg_slug = None

  def _import_workflow(self, import_data, expected_resp_action):
    """Import Workflow with ACL parameters.

    After performing import, check that its response is equal expected one.

    Args:
        import_data: Dictionary contains 2 lists.
            'admins': Test people data indexes who should get Admin role.
            'members': Test people data indexes who should get Member role.
        expected_resp_action: Action which was performed on imported item.
    """
    wf_title = ggrc_factories.random_str(chars=string.ascii_letters)
    wf_import_params = collections.OrderedDict([
        ("object_type", "Workflow"),
        ("title", wf_title),
        ("Need Verification", 'True')
    ])
    if import_data['members']:
      import_members = '\n'.join(
          self.user_emails[idx] for idx in import_data['members'])
      wf_import_params['workflow member'] = import_members
    if import_data['admins']:
      import_admins = '\n'.join(
          self.user_emails[idx] for idx in import_data['admins'])
      wf_import_params['admin'] = import_admins
    wf_import_params['code'] = self.wf_slug
    response = self.import_data(wf_import_params)
    if expected_resp_action != 'ignored':
      self._check_csv_response(response, {})
    if not self.wf_slug and expected_resp_action != 'ignored':
      workflow = Workflow.query.filter(Workflow.title == wf_title).one()
      self.wf_slug = workflow.slug
    self.assertEqual(response[0][expected_resp_action], 1)

  def _import_task_group(self, assignee_id, expected_resp_action):
    """Import TaskGroup with provided assignee.

    After performing import, check that its response is equal expected one.

    Args:
        assignee_id: Test people data index who should become TG assignee.
        expected_resp_action: Action which was performed on imported item.
    """
    tg_data = collections.OrderedDict([
        ("object_type", TaskGroup.__name__),
        ("workflow", self.wf_slug),
        ("assignee", self.user_emails[assignee_id]),
        ("title", "TG SomeTitle"),
    ])
    tg_data['code'] = self.tg_slug
    response = self.import_data(tg_data)
    self._check_csv_response(response, {})
    if not self.tg_slug:
      workflow = Workflow.query.filter(Workflow.slug == self.wf_slug).one()
      task_group = TaskGroup.query.filter(
          TaskGroup.workflow_id == workflow.id
      ).one()
      self.tg_slug = task_group.slug
    self.assertEqual(response[0][expected_resp_action], 1)

  def _check_workflow_acl(self, expected_data):
    """Check that actual Workflow ACL equals expected test data.

    Args:
        expected_data: Dictionary contains 2 lists.
            'admins': Test people data indexes who should get Admin role.
            'members': Test people data indexes who should get Member role.
    """
    workflow = Workflow.eager_query().filter(
        Workflow.slug == self.wf_slug).first()
    exst_admins = [
        acp.person.email
        for acp in workflow.acr_name_acl_map['Admin'].access_control_people
    ]
    expected_admins = [self.user_emails[idx]
                       for idx in expected_data['admins']]
    self.assertItemsEqual(exst_admins, expected_admins)
    wf_members = workflow.acr_name_acl_map['Workflow Member']
    exst_members = [
        acp.person.email
        for acp in wf_members.access_control_people
    ]
    expected_members = [self.user_emails[idx]
                        for idx in expected_data['members']]
    self.assertItemsEqual(exst_members, expected_members)

  def _check_propagated_acl(self, exp_admin_ids, exp_member_ids):
    """Check that roles were propagated properly.

    Args:
        exp_admin_ids: Test people data indexes who should get Admin role.
        exp_member_ids: Test people data indexes who should get Member role.
    """
    workflow = Workflow.query.filter(Workflow.slug == self.wf_slug).one()
    task_group = TaskGroup.query.filter(
        TaskGroup.workflow_id == workflow.id,
        TaskGroup.slug == self.tg_slug
    ).one()

    acl = AccessControlList.eager_query().filter(
        AccessControlList.object_type == TaskGroup.__name__,
        AccessControlList.object_id == task_group.id
    ).all()
    propagated_admins = [acl for a in acl
                         if a.ac_role.name.startswith("Admin*")]
    self.assertEqual(len(propagated_admins), 1)

    propagated_members = [acl for a in acl
                          if a.ac_role.name.startswith("Workflow Member*")]
    self.assertEqual(len(propagated_members), 1)

  @ddt.data(
      {
          'import_data': {
              'admins': [0, 1],
              'members': [2, 3]
          },
          'expected_data': {
              'admins': [0, 1],
              'members': [2, 3]
          }
      },
      {
          'import_data': {
              'admins': [0, 1],
              'members': [1, 2]
          },
          'expected_data': {
              'admins': [0, 1],
              'members': [1, 2]
          }
      },
      {
          'import_data': {
              'admins': [0, 1],
              'members': [0, 1]
          },
          'expected_data': {
              'admins': [0, 1],
              'members': [0, 1]
          }
      },
      {
          'import_data': {
              'admins': [0, 1],
              'members': []
          },
          'expected_data': {
              'admins': [0, 1],
              'members': []
          }
      },
      {
          'import_data': {
              'admins': [],
              'members': [0, 1]
          },
          'expected_resp_action': 'ignored'
      },
      {
          'import_data': {
              'admins': [],
              'members': []
          },
          'expected_resp_action': 'ignored'
      }
  )
  @ddt.unpack
  def test_create_workflow_with_acl(self, import_data, expected_data=None,
                                    expected_resp_action='created'):
    """Tests create Workflow with data: {import_data}."""
    self._import_workflow(import_data, expected_resp_action)
    if expected_resp_action != 'ignored':
      self._check_workflow_acl(expected_data)

  @ddt.data(
      {
          'import_data': {
              'admins': [0, 1],
              'members': [2, 3]
          },
          'expected_data': {
              'admins': [0, 1],
              'members': [2, 3]
          }
      },
      {
          'import_data': {
              'admins': [0, 1],
              'members': [1, 2]
          },
          'expected_data': {
              'admins': [0, 1],
              'members': [1, 2]
          }
      },
      {
          'import_data': {
              'admins': [0, 1],
              'members': [0, 1]
          },
          'expected_data': {
              'admins': [0, 1],
              'members': [0, 1]
          }
      },
      {
          'import_data': {
              'admins': [0, 1],
              'members': []
          },
          'expected_data': {
              'admins': [0, 1],
              'members': [6, 7]
          }
      },
      {
          'import_data': {
              'admins': [6, 7],
              'members': []
          },
          'expected_data': {
              'admins': [6, 7],
              'members': [6, 7]
          }
      },
      {
          'import_data': {
              'admins': [],
              'members': [0, 1]
          },
          'expected_data': {
              'admins': [4, 5],
              'members': [0, 1]
          }
      },
      {
          'import_data': {
              'admins': [],
              'members': []
          },
          'expected_data': {
              'admins': [4, 5],
              'members': [6, 7]
          }
      },
      {
          'import_data': {
              'admins': [],
              'members': [4, 5]
          },
          'expected_data': {
              'admins': [4, 5],
              'members': [4, 5]
          }
      }
  )
  @ddt.unpack
  def test_update_workflow_acl(self, import_data, expected_data):
    """Tests update existing Workflow with data: {import_data}."""
    # Create Workflow
    self._import_workflow({'admins': [4, 5], 'members': [6, 7]}, 'created')
    # Update Workflow ACL and check the results
    self._import_workflow(import_data, 'updated')
    self._check_workflow_acl(expected_data)

  def test_propagate_workflow_acl_on_tg_create(self):
    """Tests propagate Workflow's ACL records on TaskGroup create."""
    self._import_workflow({'admins': [1, 2], 'members': [3, 4]}, 'created')
    self._import_task_group(5, 'created')
    self._check_propagated_acl([1, 2], [3, 4, 5])

  def test_propagate_workflow_acl_on_workflow_acl_update(self):
    """Tests propagate Workflow ACL records on update Workflow ACL."""
    # Create Workflow with TaskGroup
    self._import_workflow({'admins': [1, 2], 'members': [3, 4]}, 'created')
    self._import_task_group(5, 'created')

    # Update previously created Workflow with updated ACL and check the results
    self._import_workflow({'admins': [6], 'members': [7]}, 'updated')
    self._check_propagated_acl([6], [7])

  def test_add_member_role_to_tg_assignee_on_create(self):
    """Tests TaskGroup assignee has member role on TaskGroup create."""
    # Create Workflow with TaskGroup
    self._import_workflow({'admins': [1, 2], 'members': [3, 4]}, 'created')
    self._import_task_group(5, 'created')
    self._check_workflow_acl({'admins': [1, 2], 'members': [3, 4, 5]})

  def test_add_member_role_to_tg_assignee_on_update(self):
    """Tests TaskGroup assignee has member role on TaskGroup update."""
    # Create Workflow with TaskGroup
    self._import_workflow({'admins': [1, 2], 'members': [3, 4]}, 'created')
    self._import_task_group(5, 'created')
    self._check_workflow_acl({'admins': [1, 2], 'members': [3, 4, 5]})

    # Update TaskGroup with new assignee
    self._import_task_group(6, 'updated')
    self._check_workflow_acl({'admins': [1, 2], 'members': [3, 4, 5, 6]})
