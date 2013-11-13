# Copyright (C) 2013 Google Inc., authors, and contributors <see AUTHORS file>
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>
# Created By: david@reciprocitylabs.com
# Maintained By: david@reciprocitylabs.com

from ggrc import db
from ggrc.models.all_models import all_models
from ggrc.models.object_person import ObjectPerson
from ggrc.models.object_owner import ObjectOwner
from ggrc.models.request import Request
from ggrc.models.response import Response
from ggrc_basic_permissions.models import UserRole
from ggrc.rbac import permissions, context_query_filter
from sqlalchemy import event, and_, or_, text
from sqlalchemy.schema import DDL
from .sql import SqlIndexer

class MysqlRecordProperty(db.Model):
  __tablename__ = 'fulltext_record_properties'
  __table_args__ = {'mysql_engine': 'myisam'}

  key = db.Column(db.Integer, primary_key=True)
  type = db.Column(db.String(64), primary_key=True)
  context_id = db.Column(db.Integer)
  tags = db.Column(db.String)
  property = db.Column(db.String(64), primary_key=True)
  content = db.Column(db.Text)

event.listen(
    MysqlRecordProperty.__table__,
    'after_create',
    DDL('ALTER TABLE {tablename} ADD FULLTEXT INDEX {tablename}_text_idx '
      '(content)'.format(tablename=MysqlRecordProperty.__tablename__))
    )

class MysqlIndexer(SqlIndexer):
  record_type = MysqlRecordProperty

  def _get_type_query(
      self, types=None, permission_type='read', permission_model=None):
    model_names = [model.__name__ for model in all_models]
    if types is not None:
      model_names = [m for m in model_names if m in types]

    type_queries = []
    for model_name in model_names:
      type_query = None
      if permission_type == 'read':
        contexts = permissions.read_contexts_for(
            permission_model or model_name)
      elif permission_type == 'create':
        contexts = permissions.create_contexts_for(
            permission_model or model_name)
      elif permission_type == 'update':
        contexts = permissions.update_contexts_for(
            permission_model or model_name)
      elif permission_type == 'delete':
        contexts = permissions.delete_contexts_for(
            permission_model or model_name)

      if permission_model and contexts:
        contexts = set(contexts) & set(
            permissions.read_contexts_for(model_name))

      type_query = and_(
          MysqlRecordProperty.type == model_name,
          context_query_filter(MysqlRecordProperty.context_id, contexts))
      type_queries.append(type_query)

    return and_(
        MysqlRecordProperty.type.in_(model_names),
        or_(*type_queries))

  def _get_filter_query(self, terms):
    if not terms:
      return True
    # FIXME: Temporary (slow) fix for words shorter than MySQL default limit
    elif len(terms) < 4:
      return MysqlRecordProperty.content.contains(terms)
    else:
      return MysqlRecordProperty.content.match(terms)

  # filters by "myview" for a given person
  def _get_owner_query(self, types=None, owner_id=None):
    if not owner_id:
      return True

    model_names = [model.__name__ for model in all_models]
    if types is not None:
      model_names = [m for m in model_names if m in types]

    owner_queries = []
    for model_name in model_names:
      model = [x for x in all_models if x.__name__ == model_name][0]
      if model_name is 'Person':
        filter_query = False
      else:
        filter_query = and_(
          MysqlRecordProperty.type == model_name,
          or_(
            MysqlRecordProperty.context_id.in_(
              db.session.query(UserRole.context_id).filter(
                and_(
                  UserRole.person_id == owner_id, 
                  UserRole.context_id != None
                )
              ).distinct()
            ),
            MysqlRecordProperty.key.in_(
              db.session.query(model.id).filter(model.owner_id == owner_id)
            ),
            MysqlRecordProperty.key.in_(
              db.session.query(ObjectPerson.personable_id).filter(
                and_(
                  ObjectPerson.person_id == owner_id, 
                  ObjectPerson.personable_type == model_name
                )
              )
            ),
            MysqlRecordProperty.key.in_(
              db.session.query(ObjectOwner.ownable_id).filter(
                and_(
                  ObjectOwner.person_id == owner_id,
                  ObjectOwner.ownable_type == model_name
                )
              )
            ),
            and_(
              model_name == 'Audit',
              MysqlRecordProperty.key.in_(
                db.session.query(Request.audit_id).filter(
                  or_(
                    Request.assignee_id == owner_id, 
                    Request.id.in_(
                      db.session.query(Response.request_id).filter(
                        Response.owner_id == owner_id
                      ).distinct()
                    )
                  )
                ).distinct()
              )
            )
          )
        )
      owner_queries.append(filter_query)

    return and_(
        MysqlRecordProperty.type.in_(model_names),
        or_(*owner_queries))

  def search(
      self, terms, types=None, permission_type='read', permission_model=None, owner_id=None):
    query = self._get_type_query(types, permission_type, permission_model)
    query = and_(query, self._get_filter_query(terms))
    query = and_(query, self._get_owner_query(types, owner_id))
    return db.session.query(self.record_type).filter(query)

  def counts(self, terms, group_by_type=True, types=None, owner_id=None):
    from sqlalchemy import func, distinct

    query = db.session.query(
        self.record_type.type, func.count(distinct(self.record_type.key)))
    query = query.filter(self._get_type_query(types))
    query = query.filter(self._get_filter_query(terms))
    query = query.filter(self._get_owner_query(types, owner_id))
    query = query.group_by(self.record_type.type)
    # FIXME: Is this needed for correct group_by/count-distinct behavior?
    #query = query.order_by(self.record_type.type, self.record_type.key)
    return query.all()

Indexer = MysqlIndexer
