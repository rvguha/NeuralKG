(function (root) {
  'use strict';

  var DB_NAME = 'neural-kg-chat';
  var DB_VERSION = 1;
  var STORE = 'turns';
  var SESSION_KEY = 'neural-kg.conversation-id';

  function requestPromise(request) {
    return new Promise(function (resolve, reject) {
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { reject(request.error || new Error('IndexedDB request failed')); };
    });
  }

  function transactionPromise(transaction) {
    return new Promise(function (resolve, reject) {
      transaction.oncomplete = function () { resolve(); };
      transaction.onabort = transaction.onerror = function () {
        reject(transaction.error || new Error('IndexedDB transaction failed'));
      };
    });
  }

  function uuid() {
    if (root.crypto && typeof root.crypto.randomUUID === 'function') return root.crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 3 | 8)).toString(16);
    });
  }

  function ChatTurnStore(indexedDB, keyRange) {
    this.indexedDB = indexedDB || root.indexedDB;
    this.keyRange = keyRange || root.IDBKeyRange;
    this.dbPromise = null;
    this.conversations = Object.create(null);
    this.hydratePromise = null;
  }

  ChatTurnStore.prototype.open = function () {
    var self = this;
    if (self.dbPromise) return self.dbPromise;
    if (!self.indexedDB) return Promise.reject(new Error('IndexedDB is unavailable'));
    self.dbPromise = new Promise(function (resolve, reject) {
      var request = self.indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = function () {
        var db = request.result;
        var store = db.objectStoreNames.contains(STORE)
          ? request.transaction.objectStore(STORE)
          : db.createObjectStore(STORE, {keyPath: ['conversation_id', 'turn_number']});
        if (!store.indexNames.contains('conversation_id'))
          store.createIndex('conversation_id', 'conversation_id', {unique: false});
        if (!store.indexNames.contains('started_at'))
          store.createIndex('started_at', 'started_at', {unique: false});
        if (!store.indexNames.contains('completed_at'))
          store.createIndex('completed_at', 'completed_at', {unique: false});
      };
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () {
        self.dbPromise = null;
        reject(request.error || new Error('Could not open chat history'));
      };
    });
    return self.dbPromise;
  };

  ChatTurnStore.prototype.hydrate = function () {
    var self = this;
    if (self.hydratePromise) return self.hydratePromise;
    self.hydratePromise = self.open().then(function (db) {
      var transaction = db.transaction(STORE, 'readonly');
      return requestPromise(transaction.objectStore(STORE).getAll());
    }).then(function (rows) {
      var conversations = Object.create(null);
      rows.forEach(function (turn) {
        var id = turn.conversation_id;
        if (!conversations[id]) conversations[id] = {conversation_id: id, turns: []};
        conversations[id].turns.push(turn);
      });
      Object.keys(conversations).forEach(function (id) {
        conversations[id].turns.sort(function (a, b) { return a.turn_number - b.turn_number; });
      });
      self.conversations = conversations;
      return conversations;
    }).catch(function (error) {
      self.hydratePromise = null;
      throw error;
    });
    return self.hydratePromise;
  };

  ChatTurnStore.prototype.memoryStore = async function () {
    await this.hydrate();
    return this.conversations;
  };

  ChatTurnStore.prototype._remember = function (record) {
    var id = record.conversation_id;
    var conversation = this.conversations[id];
    if (!conversation) conversation = this.conversations[id] = {conversation_id: id, turns: []};
    var replaced = false;
    conversation.turns = conversation.turns.map(function (turn) {
      if (turn.turn_number !== record.turn_number) return turn;
      replaced = true;
      return record;
    });
    if (!replaced) conversation.turns.push(record);
    conversation.turns.sort(function (a, b) { return a.turn_number - b.turn_number; });
    return record;
  };

  ChatTurnStore.prototype.conversationId = function () {
    var value = null;
    try { value = root.sessionStorage && root.sessionStorage.getItem(SESSION_KEY); } catch (_) {}
    if (!value) {
      value = uuid();
      try { if (root.sessionStorage) root.sessionStorage.setItem(SESSION_KEY, value); } catch (_) {}
    }
    return value;
  };

  ChatTurnStore.prototype.newConversation = function () {
    var value = uuid();
    try { if (root.sessionStorage) root.sessionStorage.setItem(SESSION_KEY, value); } catch (_) {}
    return value;
  };

  ChatTurnStore.prototype.selectConversation = function (conversationId) {
    if (!conversationId) throw new Error('conversation id is required');
    try { if (root.sessionStorage) root.sessionStorage.setItem(SESSION_KEY, conversationId); } catch (_) {}
    return conversationId;
  };

  ChatTurnStore.prototype.listConversations = async function () {
    await this.hydrate();
    return Object.keys(this.conversations).map(function (id) {
      return this.conversations[id];
    }, this).sort(function (a, b) {
      var at = a.turns.length ? a.turns[a.turns.length - 1].started_at : '';
      var bt = b.turns.length ? b.turns[b.turns.length - 1].started_at : '';
      return String(bt).localeCompare(String(at));
    });
  };

  ChatTurnStore.prototype.nextTurnNumber = async function (conversationId) {
    await this.hydrate();
    var conversation = this.conversations[conversationId];
    var turns = conversation ? conversation.turns : [];
    return turns.length ? Number(turns[turns.length - 1].turn_number) + 1 : 1;
  };

  ChatTurnStore.prototype.put = async function (record) {
    await this.hydrate();
    var db = await this.open();
    var transaction = db.transaction(STORE, 'readwrite');
    transaction.objectStore(STORE).put(record);
    await transactionPromise(transaction);
    return this._remember(record);
  };

  ChatTurnStore.prototype.beginTurn = function (conversationId, turnNumber, prompt, startedAt) {
    return this.put({
      conversation_id: conversationId,
      turn_number: turnNumber,
      prompt: prompt,
      answer: null,
      status: 'in_progress',
      started_at: startedAt || new Date().toISOString(),
      completed_at: null,
      terminal: null,
      candidates: [],
      messages: [],
      error: null
    });
  };

  ChatTurnStore.prototype.finishTurn = async function (conversationId, turnNumber, changes) {
    await this.hydrate();
    var db = await this.open();
    var transaction = db.transaction(STORE, 'readwrite');
    var store = transaction.objectStore(STORE);
    var current = await requestPromise(store.get([conversationId, turnNumber]));
    var record = Object.assign({}, current || {
      conversation_id: conversationId,
      turn_number: turnNumber
    }, changes || {});
    if (!record.completed_at) record.completed_at = new Date().toISOString();
    store.put(record);
    await transactionPromise(transaction);
    return this._remember(record);
  };

  ChatTurnStore.prototype.listConversation = async function (conversationId) {
    await this.hydrate();
    var conversation = this.conversations[conversationId];
    return conversation ? conversation.turns.slice() : [];
  };

  ChatTurnStore.prototype.deleteConversation = async function (conversationId) {
    var rows = await this.listConversation(conversationId);
    var db = await this.open();
    var transaction = db.transaction(STORE, 'readwrite');
    var store = transaction.objectStore(STORE);
    rows.forEach(function (row) { store.delete([conversationId, row.turn_number]); });
    await transactionPromise(transaction);
    delete this.conversations[conversationId];
  };

  root.NeuralKGChatHistory = {
    DB_NAME: DB_NAME,
    DB_VERSION: DB_VERSION,
    STORE: STORE,
    ChatTurnStore: ChatTurnStore
  };
})(typeof window !== 'undefined' ? window : globalThis);
