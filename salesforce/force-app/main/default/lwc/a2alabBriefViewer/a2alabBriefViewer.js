import { LightningElement, api, wire } from 'lwc';
import { getRecord, getFieldValue } from 'lightning/uiRecordApi';
import { mdToHtml } from 'c/a2alabMarkdown';

import BRIEF_FIELD from '@salesforce/schema/A2ALab_Account_Brief__c.Brief__c';
import DATE_FIELD from '@salesforce/schema/A2ALab_Account_Brief__c.Brief_Date__c';
import SOURCE_FIELD from '@salesforce/schema/A2ALab_Account_Brief__c.Source__c';
import SESSION_FIELD from '@salesforce/schema/A2ALab_Account_Brief__c.Research_Session_Id__c';
import ACCOUNT_FIELD from '@salesforce/schema/A2ALab_Account_Brief__c.Account__c';
import ACCOUNT_NAME_FIELD from '@salesforce/schema/A2ALab_Account_Brief__c.Account__r.Name';

const FIELDS = [BRIEF_FIELD, DATE_FIELD, SOURCE_FIELD, SESSION_FIELD, ACCOUNT_FIELD, ACCOUNT_NAME_FIELD];

export default class A2alabBriefViewer extends LightningElement {
    @api recordId;
    record;
    error;

    @wire(getRecord, { recordId: '$recordId', fields: FIELDS })
    wired({ data, error }) {
        if (data) {
            this.record = data;
            this.error = undefined;
        } else if (error) {
            const body = error.body || error;
            this.error = (body.message || JSON.stringify(body)).slice(0, 300);
        }
    }

    get briefHtml() {
        return this.record ? mdToHtml(getFieldValue(this.record, BRIEF_FIELD) || '(empty brief)') : '';
    }

    get briefDate() {
        return this.record ? getFieldValue(this.record, DATE_FIELD) : '';
    }

    get source() {
        return this.record ? getFieldValue(this.record, SOURCE_FIELD) : '';
    }

    get session() {
        return this.record ? getFieldValue(this.record, SESSION_FIELD) : '';
    }

    get accountUrl() {
        const id = this.record ? getFieldValue(this.record, ACCOUNT_FIELD) : null;
        return id ? '/' + id : null;
    }

    get accountName() {
        return this.record ? getFieldValue(this.record, ACCOUNT_NAME_FIELD) : '';
    }

    get ready() {
        return Boolean(this.record);
    }
}
