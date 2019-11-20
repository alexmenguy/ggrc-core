# Copyright (C) 2019 Google Inc.
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>

"""
  Assessment generator hooks

  We are applying assessment template properties and make
  new relationships and custom attributes
"""

from datetime import datetime
import collections
import itertools
import logging

import flask

from ggrc import access_control
from ggrc import db
from ggrc import login
from ggrc import utils
from ggrc.access_control import list as ggrc_acl
from ggrc.access_control import people as ggrc_acp
from ggrc.login import get_current_user_id
from ggrc.models import all_models
from ggrc.models import cache as ggrc_cache
from ggrc.models import custom_attribute_definition as ggrc_cad
from ggrc.models.hooks import common
from ggrc.models.hooks.issue_tracker import assessment_integration
from ggrc.models.hooks.issue_tracker import integration_utils
from ggrc.models.exceptions import StatusValidationError
from ggrc.services import signals
from ggrc.utils import referenced_objects


logger = logging.getLogger(__name__)


def _validate_assessment_done_state(old_value, obj):
  """Checks if it's allowed to set done state from not done."""
  new_value = obj.status
  if old_value in obj.NOT_DONE_STATES and \
     new_value in obj.DONE_STATES:
    if hasattr(obj, "preconditions_failed") and obj.preconditions_failed:
      raise StatusValidationError("CA-introduced completion "
                                  "preconditions are not satisfied. "
                                  "Check preconditions_failed "
                                  "of items of self.custom_attribute_values")
  if (obj.status == obj.FINAL_STATE and
     not obj.verified and
     not getattr(obj, 'sox_302_enabled', False) and
     getattr(obj, 'verifiers', [])):
    obj.status = obj.DONE_STATE


def _get_audit_id(asmt_src):
  # type: (dict) -> Optional[int]
  """Get audit's ID from assessment source or None."""
  return (asmt_src.get("audit") or {}).get("id")


def _get_snapshot_id(asmt_src):
  # type: (dict) -> Optional[int]
  """Get snapshot's ID from assessment source or None."""
  return (asmt_src.get("object") or {}).get("id")


def _get_template_id(asmt_src):
  # type: (dict) -> Optional[int]
  """Get template's ID from assessment source or None."""
  return (asmt_src.get("template") or {}).get("id")


def _get_object_from_src(src, id_getter, obj_type, current=None):
  # type: (dict, function, str, Optional[db.Model]) -> Optional[db.Model]
  """Get object from source."""
  obj = current
  id_ = id_getter(src)
  if id_ is None:
    return None
  if current is None or current.id != id_:
    obj = referenced_objects.get(obj_type, id_, raise_exception=True)
  return obj


def _is_autogenerated(asmt_src):
  # type: (dict) -> bool
  """Check if assessment is autogenerated."""
  return asmt_src.get("_generated") or False


def set_title(assessment, audit, snapshot_rev_content):
  # type: (models.Assessment, models.Audit, dict) -> None
  """Set title for assessment from audit and snapshot's revision."""
  assessment.title = u'{} assessment for {}'.format(
      snapshot_rev_content['title'],
      audit.title,
  )


def set_test_plan(assessment, template, snapshot_rev_content):
  # type: (models.Assessment, models.AssessmentTemplate, dict) -> None
  """Set test plan for assessment from template and snapshot's revision."""
  if template:
    assessment.test_plan_procedure = template.test_plan_procedure
    assessment.test_plan = template.procedure_description
  if not template or template.test_plan_procedure:
    snapshot_plan = snapshot_rev_content.get("test_plan", "")
    if assessment.test_plan and snapshot_plan:
      assessment.test_plan += "<br>"
      assessment.test_plan += snapshot_plan
    elif snapshot_plan:
      assessment.test_plan = snapshot_plan


def set_assessment_type(assessment, template):
  # type: (models.Assessment, models.AssessmentTemplate) -> None
  """Set assessment type from template."""
  if template and template.template_object_type:
    assessment.assessment_type = template.template_object_type


def set_sox_302_enabled(assessment, template):
   # type: (models.Assessment, models.AssessmentTemplate) -> None
  """Set sox_302_enabled flag from template."""
  if template:
    assessment.sox_302_enabled = template.sox_302_enabled


# pylint: disable=too-many-arguments
def _handle_assessment(assessment,  # type: models.Assessment
                       audit,  # type: models.Audit
                       tmpl=None,  # type: Optional[models.AssessmentTemplate]
                       snapshot=None,  # type: Optional[models.Snapshot]
                       snapshot_rev_content=None,  # type: Optional[dict]
                       is_autogenerated=False,  # type: bool
                       ):
  # type: (...) -> None
  """Handles auto calculated properties for Assessment model."""
  assessment.map_to(snapshot)
  assessment.map_to(audit)

  if tmpl is not None:
    set_sox_302_enabled(assessment, tmpl)
    _mark_cads_to_batch_insert(
        ca_definitions=tmpl.custom_attribute_definitions,
        attributable=assessment,
    )

  if not (is_autogenerated or snapshot):
    return

  _relate_assignees(assessment, snapshot, snapshot_rev_content, tmpl, audit)

  set_title(assessment, audit, snapshot_rev_content)
  set_test_plan(assessment, tmpl, snapshot_rev_content)
  set_assessment_type(assessment, tmpl)


def init_hook():
  """Initializes hooks."""

  # pylint: disable=unused-variable
  @signals.Restful.collection_posted.connect_via(all_models.Assessment)
  def handle_assessment_post(sender, objects=None, sources=None, service=None):
    """Applies custom attribute definitions and maps people roles.

    Applicable when generating Assessment with template.

    Args:
      sender: A class of Resource handling the POST request.
      objects: A list of model instances created from the POSTed JSON.
      sources: A list of original POSTed JSON dictionaries.
    """
    del sender, service  # Unused

    db.session.flush()

    audit, template = None, None
    for assessment, src in itertools.izip(objects, sources):
      audit = _get_object_from_src(
          src, _get_audit_id, all_models.Audit, current=audit)

      template = _get_object_from_src(
          src, _get_template_id, all_models.AssessmentTemplate,
          current=template)

      snapshot_rev_content = None
      snapshot = _get_object_from_src(
          src, _get_snapshot_id, all_models.Snapshot)
      if snapshot is not None:
        # Since every content call on revision leads to it calculation (and
        # one is quite expensive) it is better for performance to compute it
        # one time and pass it everywhere though it may seem redundant.
        snapshot_rev_content = snapshot.revision.content

      _handle_assessment(assessment,
                         audit,
                         tmpl=template,
                         snapshot=snapshot,
                         snapshot_rev_content=snapshot_rev_content,
                         is_autogenerated=_is_autogenerated(src),
                         )

    _batch_insert_cads(attributables=objects)
    _batch_insert_acps(assessments=objects)

    # Flush roles objects for generated assessments.
    db.session.flush()

    tracker_handler = assessment_integration.AssessmentTrackerHandler()
    for assessment, src in itertools.izip(objects, sources):
      # Handling IssueTracker info here rather than in hooks/issue_tracker
      # would avoid querying same data (such as snapshots, audits and
      # templates) twice.
      integration_utils.update_issue_tracker_for_import(assessment)
      tracker_handler.handle_assessment_create(assessment, src)

  # pylint: disable=unused-variable
  @signals.Restful.model_put.connect_via(all_models.Assessment)
  def handle_assessment_put(sender, obj=None, src=None, service=None):
    """Handles assessment update event."""
    del sender, src, service  # Unused
    common.ensure_field_not_changed(obj, 'audit')

  @signals.Restful.model_put_before_commit.connect_via(all_models.Assessment)
  def handle_assessment_done_state(sender, **kwargs):
    """Checks if it's allowed to set done state from not done."""
    del sender  # Unused arg
    obj = kwargs['obj']
    initial_state = kwargs['initial_state']
    old_value = initial_state.status
    try:
      _validate_assessment_done_state(old_value, obj)
    except StatusValidationError as error:
      db.session.rollback()
      raise error


def _generate_assignee_relations(assessment,
                                 assignee_ids,
                                 verifier_ids,
                                 creator_ids):
  """Generates db relations to assessment for sent role ids.

    Args:
        assessment (model instance): Assessment model
        assignee_ids (list): list of person ids
        verifier_ids (list): list of person ids
        creator_ids (list): list of person ids
  """
  people = set(assignee_ids + verifier_ids + creator_ids)
  person_dict = {i.id: i for i in all_models.Person.query.filter(
      all_models.Person.id.in_(people)
  )}

  for person_id in people:
    person = person_dict.get(person_id)
    if person is None:
      continue
    if person.id in assignee_ids:
      _mark_acps_to_batch_insert(person, "Assignees", assessment)
    if person_id in verifier_ids:
      _mark_acps_to_batch_insert(person, "Verifiers", assessment)
    if person_id in creator_ids:
      _mark_acps_to_batch_insert(person, "Creators", assessment)


def _get_people_ids_by_role(role_name, defaul_role_name, template_settings,
                            role_name_people_id_map):
  # type: (str, str, Dict[str, Any], Dict[str, List[int]]) -> List[int]
  """Get people IDs by role name from role_name_people_id_map."""
  if not template_settings.get(role_name):
    return []
  template_role = template_settings[role_name]
  if isinstance(template_role, list):
    return template_role

  default_people = role_name_people_id_map.get(defaul_role_name)
  return role_name_people_id_map.get(template_role, default_people) or []


def _generate_role_people_map(audit, snapshot, snapshot_rev_content):
  # type: (models.Audit, models.Snapshot, dict) -> Dict[str, int]
  """Generate role_name-people_ids dict from audit and snapshot."""
  acr_dict = access_control.role.get_custom_roles_for(snapshot.child_type)
  audit_acr = access_control.role.get_custom_roles_for("Audit")
  auditor_role_id = next(id_ for id_, role_name in audit_acr.iteritems()
                         if role_name == "Auditors")
  captain_role_id = next(id_ for id_, role_name in audit_acr.iteritems()
                         if role_name == "Audit Captains")

  role_name_person_id_map = collections.defaultdict(list)
  for acl in snapshot_rev_content["access_control_list"]:
    acr = acr_dict.get(acl["ac_role_id"])
    if not acr:
      # This can happen when we try to create an assessment for a control that
      # had a custom attribute role removed. This can not cause a bug as we
      # only use the acl_list for getting new assessment assignees and those
      # can only be from non editable roles, meaning the roles that we actually
      # need can not be removed. Non essential roles that are removed might
      # should not affect this assessment generation.
      logger.info("Snapshot %d contains deleted role %d",
                  snapshot.id, acl["ac_role_id"])
      continue
    role_name_person_id_map[acr].append(acl["person_id"])

  role_name_person_id_map["Audit Lead"].extend([
      person.id for person, acl in audit.access_control_list
      if acl.ac_role_id == captain_role_id
  ])
  role_name_person_id_map["Auditors"].extend([
      person.id for person, acl in audit.access_control_list
      if acl.ac_role_id == auditor_role_id
  ])

  if not role_name_person_id_map["Auditors"]:
    # If assessment is being generated with a snapshot and there is no auditors
    # on it's audit, audit captains should be taken as auditors.
    role_name_person_id_map["Auditors"].extend(
        role_name_person_id_map["Audit Lead"],
    )

  return role_name_person_id_map


def _relate_assignees(assessment,  # type: models.Assessment
                      snapshot,  # type: models.Snapshot
                      snapshot_rev_content,  # type: Dict[str, Any]
                      template,  # type: models.AssessmentTemplate
                      audit  # type: models.Audit
                      ):
  # type: (...) -> None
  """Relate assignees from audit, snapshot, and template to assessments.

  Relate people assigned to audit, snapshot and template to assessment. People
  will be taken from specific roles on audit and snapshot or directly from the
  template according to template settings.

  Args:
    assessment (models.Assessment): Assessment model instance.
    snapshot (models.Snapshot): Snapshot model instance used during assessment
      generation.
    snapshot_rev_content (dict): Dict with content of snapshot's revision. It
      is passed here directly instead of getting it from snapshot it is better
      for performance to compute it one time and pass it everywhere.
    template (models.AssessmentTempalte): AssessmentTemplate instance used
      during assessment generation.
    audit (models.Audit): Audit instance the assessment is generated for.

  Returns:
    None.
  """
  if template is not None:
    template_settings = template.default_people
  else:
    template_settings = {
        "assignees": "Principal Assignees",
        "verifiers": "Auditors",
    }

  role_people_map = _generate_role_people_map(
      audit, snapshot, snapshot_rev_content)
  assignee_ids = _get_people_ids_by_role(
      "assignees", "Audit Lead", template_settings, role_people_map)
  verifier_ids = _get_people_ids_by_role(
      "verifiers", "Auditors", template_settings, role_people_map)

  _generate_assignee_relations(
      assessment, assignee_ids, verifier_ids, [get_current_user_id()])


def _mark_cads_to_batch_insert(ca_definitions, attributable):
  """Mark custom attribute definitions for batch insert.

  Create stubs of `ca_defintions` with definition set to `attributable` and add
  them to `flask.g.cads_to_batch_insert` list. All CAD stubs presented in
  `flask.g.cads_to_batch_insert` will be inserted in custom attribute
  defintiions table upon `_batch_insert_cads` call.

  Args:
    ca_definitions (List[models.CustomAttributeDefinition]): List of CADs to
      be marked for batch insert.
    attributable (db.Model): Model instance for which CADs should be created.
  """

  def clone_cad_stub(cad_stub, target):
    """Create a copy of `cad_stub` CAD and assign it to `target`."""
    now = datetime.utcnow()
    current_user_id = login.get_current_user_id()

    clone_stub = dict(cad_stub)
    clone_stub["definition_type"] = target._inflector.table_singular
    clone_stub["definition_id"] = target.id
    clone_stub["created_at"] = now
    clone_stub["updated_at"] = now
    clone_stub["modified_by_id"] = current_user_id
    clone_stub["id"] = None

    return clone_stub

  if not hasattr(flask.g, "cads_to_batch_insert"):
    flask.g.cads_to_batch_insert = []

  for ca_definition in ca_definitions:
    stub = ca_definition.to_dict()
    new_ca_stub = clone_cad_stub(stub, attributable)
    flask.g.cads_to_batch_insert.append(new_ca_stub)


def _batch_insert_cads(attributables):
  """Insert custom attribute definitions marked for batch insert.

  Insert CADs stored in `flask.g.cads_to_batch_insert` in custom attribute
  definitions table. Attributables are passed here to obtain inserted CADs from
  DB so they could be placed in cache.

  Args:
    attributables (List[db.Model]): List of model instances for which CADs
      should be inserted.
  """
  cads_to_batch_insert = getattr(flask.g, "cads_to_batch_insert", [])
  if not cads_to_batch_insert:
    return
  with utils.benchmark("Insert CADs in batch"):
    flask.g.cads_to_batch_insert = []
    inserter = ggrc_cad.CustomAttributeDefinition.__table__.insert()
    db.session.execute(
        inserter.values([stub for stub in cads_to_batch_insert])
    )

    # Add inserted CADs into new objects collection of the cache, so that
    # they will be logged within event and appropriate revisions will be
    # created. At this point it is safe to query CADs by definition_type and
    # definition_id since this batch insert will be called only at assessment
    # creation time and there will be no other LCAs for it.
    new_cads_q = ggrc_cad.CustomAttributeDefinition.query.filter(
        ggrc_cad.CustomAttributeDefinition.definition_type == "assessment",
        ggrc_cad.CustomAttributeDefinition.definition_id.in_([
            attributable.id for attributable in attributables
        ])
    )

    _add_objects_to_cache(new_cads_q)


def _mark_acps_to_batch_insert(assignee, role_name, assessment):
  """Mark access control people for batch insert.

  Create stub of ACP with person set to `assignee` and access control role to
  ACL of `assessment` object with `role_name` role. Add created stub to
  `flask.g.acps_to_batch_insert` list. All ACP stubs presented in
  `flask.g.acps_to_batch_insert` will be inserted in access control people
  table upon `_batch_insert_acps` call.

  Args:
    assignee (models.Person): Person for new ACP.
    role_name (str): ACR role name.
    assessment (models.Assessment): Assessment where person should be added.
  """

  def add_person_to_acl(person, ac_list):
    """Add `person` person to `ac_list` ACL."""
    now = datetime.utcnow()
    current_user_id = login.get_current_user_id()
    return {
        "id": None,
        "person_id": person.id,
        "ac_list_id": ac_list.id,
        "created_at": now,
        "updated_at": now,
        "modified_by_id": current_user_id,
    }

  if not hasattr(flask.g, "acps_to_batch_insert"):
    flask.g.acps_to_batch_insert = []

  acl = assessment.get_acl_with_role_name(role_name)
  if not acl:
    return

  acp_stub = add_person_to_acl(assignee, acl)
  flask.g.acps_to_batch_insert.append(acp_stub)


def _batch_insert_acps(assessments):
  """Insert access control people marked for batch insert.

  Insert ACPs stored in `flask.g.acps_to_batch_insert` in access control people
  table. Assessments are passed here to obtain inserted ACPs from DB so they
  could be placed in cache.

  Args:
    assessments (List[models.Assessment]): List of model instances for which
      ACPs should be inserted.
  """
  acps_to_batch_insert = getattr(flask.g, "acps_to_batch_insert", [])
  if not acps_to_batch_insert:
    return
  with utils.benchmark("Insert ACPs in batch"):
    flask.g.acps_to_batch_insert = []
    inserter = ggrc_acp.AccessControlPerson.__table__.insert()
    db.session.execute(
        inserter.values([stub for stub in acps_to_batch_insert])
    )

    # Add inserted ACPs into new objects collection of the cache, so that
    # they will be logged within event and appropriate revisions will be
    # created. At this point it is safe to query ACPs by ac_list_id since
    # this batch insert will be called only at assessment creation time and
    # there will be no other ACPs for it.
    new_acls_q = db.session.query(
        ggrc_acl.AccessControlList.id,
    ).filter(
        ggrc_acl.AccessControlList.object_type == "Assessment",
        ggrc_acl.AccessControlList.object_id.in_([
            assessment.id for assessment in assessments
        ]),
    )
    new_acps_q = ggrc_acp.AccessControlPerson.query.filter(
        ggrc_acp.AccessControlPerson.ac_list_id.in_([
            new_acl.id for new_acl in new_acls_q
        ])
    )

    _add_objects_to_cache(new_acps_q)


def _add_objects_to_cache(objs_q):
  """Add objects from `objs_q` query to cache."""
  cache = ggrc_cache.Cache.get_cache(create=True)
  if cache:
    cache.new.update((obj, obj.log_json()) for obj in objs_q)


def relate_ca(assessment, template):
  """Generates custom attribute list and relates it to Assessment objects

    Args:
        assessment (model instance): Assessment model
        template: Assessment Temaplte instance (may be None)
  """
  if not template:
    return None

  created_cads = []
  for definition in template.custom_attribute_definitions:
    cad = all_models.CustomAttributeDefinition(
        title=definition.title,
        definition=assessment,
        attribute_type=definition.attribute_type,
        multi_choice_options=definition.multi_choice_options,
        multi_choice_mandatory=definition.multi_choice_mandatory,
        mandatory=definition.mandatory,
        helptext=definition.helptext,
        placeholder=definition.placeholder,
    )
    db.session.add(cad)
    created_cads.append(cad)
  return created_cads
